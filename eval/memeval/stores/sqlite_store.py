"""SQLite + vectors backend — owner: Brent (@bgibson1618). Implements ``MemoryStore``.

v1 is **stdlib-only**: rows live in ``sqlite3``; each item is embedded by a
deterministic char-n-gram feature-hashing embedder and searched by brute-force
cosine. This keeps the offline/test path zero-dependency.

The paid-path upgrade changes nothing about the ``MemoryStore`` contract: inject a
real dense embedder via ``SqliteVectorStore(embed=...)`` (Voyage ``voyage-3-large`` /
``bge-m3``, PRD §7.1; see :mod:`memeval.stores.embedders`) and later swap brute-force
for an ANN index (HNSW/FAISS). Offline similarity is fuzzy/lexical (char-n-grams catch
morphology the keyword store can't); true dense semantics arrive with the real embedder.

Query/document asymmetry (store-internal seam): a real embedder (e.g. Voyage) embeds a
stored item and a search query differently — ``input_type="document"`` vs ``"query"`` —
a retrieval-quality win. The store carries that distinction through the embed seam:
:meth:`write` embeds with ``"document"``, :meth:`search` with ``"query"``. This is
*store-internal* (NOT part of the frozen ``MemoryStore`` contract) and fully
backward-compatible: an embedder is only passed ``input_type`` when its signature
accepts it, so the offline default and any legacy one-arg ``text -> vector`` embedder
behave exactly as before.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import sqlite3
from typing import Any, Callable, Optional

from ..schema import MemoryItem, RetrievedItem

# A ``text -> vector`` callable. It MAY optionally accept a keyword-only ``input_type``
# ('document' | 'query') so a query/document-aware embedder (e.g. Voyage) can carry the
# asymmetry; the store passes ``input_type`` only when the embedder's signature accepts
# it (see ``_embedder_accepts_input_type``) and otherwise calls the legacy one-arg form,
# so older injected embedders keep working unchanged.
Embedder = Callable[..., list]


def _embedder_accepts_input_type(embed: Embedder) -> bool:
    """True if ``embed`` can be called as ``embed(text, input_type=...)``, else False.

    Lets the store pass ``input_type`` to query/document-aware embedders (the offline
    hashing default, :class:`memeval.stores.embedders.MockEmbedder`,
    :class:`~memeval.stores.embedders.VoyageEmbedder`) while staying backward-compatible
    with the original one-arg ``text -> vector`` seam — a legacy single-arg callable
    (or any object whose signature can't be read) is simply called positionally.

    A ``**kwargs`` param always qualifies. A param literally named ``input_type``
    qualifies only when it is *keyword-addressable alongside a leading text positional*:
    keyword-only, or positional-or-keyword that is **not** the first positional. A sole
    or leading positional named ``input_type`` is the embedder's *text* argument, not the
    seam — classifying it as accepting would make the store call ``embed(text,
    input_type=text)`` and collide on that one parameter (TypeError on write).
    """
    try:
        params = inspect.signature(embed).parameters
    except (TypeError, ValueError):  # builtins / C callables without a readable signature
        return False
    seen_positional = False
    for p in params.values():
        if p.kind is p.VAR_KEYWORD:
            return True
        if p.name == "input_type":
            if p.kind is p.KEYWORD_ONLY:
                return True
            if p.kind is p.POSITIONAL_OR_KEYWORD and seen_positional:
                return True
            # else: POSITIONAL_ONLY, or the leading positional -> the text arg, not the seam.
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
            seen_positional = True
    return False


class _HashingEmbedder:
    """Deterministic char-n-gram feature-hashing embedder (stdlib, offline).

    Hashes each character n-gram to a signed bucket in a fixed-dim vector and
    L2-normalizes. Gives fuzzy/morphological similarity with no model or dependency.
    Real dense embeddings are injected via ``SqliteVectorStore(embed=...)``.

    Accepts (and ignores) an optional ``input_type`` so it satisfies the same embed
    seam as the query/document-aware embedders: the offline path is identical whether
    or not the store passes ``input_type``.
    """

    def __init__(self, dim: int = 256, n: int = 3) -> None:
        self.dim, self.n = dim, n

    def __call__(self, text: str, *, input_type: Optional[str] = None) -> list:
        vec = [0.0] * self.dim
        s = (text or "").strip().lower()
        grams = [s[i : i + self.n] for i in range(len(s) - self.n + 1)]
        if not grams and s:
            grams = [s]
        for g in grams:
            h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
            vec[h % self.dim] += 1.0 if (h >> 8) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec] if norm else vec


def _estimate_tokens(content: str) -> int:
    try:
        from ..models import estimate_tokens  # shared estimate, for cross-store consistency
        return estimate_tokens(content)
    except Exception:
        return max(1, len(content or "") // 4)  # mirror models.estimate_tokens (chars // 4)


def _cosine(a: list, b: list) -> float:
    if len(a) != len(b):  # fail loud on dim drift (embedder/dim changed mid-db)
        raise ValueError(f"embedding dim mismatch: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


class SqliteVectorStore:
    """SQLite-backed ``MemoryStore`` with a dense-vector index (brute-force cosine, v1).

    ``embed`` injects an embedder (``text -> vector``); the default is the stdlib
    char-n-gram hashing embedder, so the offline path needs no dependencies.
    ``embed_model`` is a label only (real-model loading is a paid-path concern).
    An empty / no-signal query returns ``[]`` (consistent with ``MarkdownStore``).
    ``MemoryItem.embedding`` is not persisted (the vector lives in its own column).

    The embed seam carries Voyage's query/document asymmetry: stored items embed with
    ``input_type="document"``, the search query with ``input_type="query"``. The store
    passes ``input_type`` only to embedders whose signature accepts it, so a legacy
    one-arg embedder (and the offline default) behave exactly as before.
    """

    def __init__(self, path: str = ":memory:", *, embed: Optional[Embedder] = None,
                 embed_model: Optional[str] = None, dim: int = 256) -> None:
        self.path = str(path)
        self.embed_model = embed_model
        self._embed = embed or _HashingEmbedder(dim)
        # Detected once: does this embedder want the query/document ``input_type`` kwarg?
        self._embed_accepts_input_type = _embedder_accepts_input_type(self._embed)
        self._conn = sqlite3.connect(self.path)
        # WAL journal mode (ADR-P2: mandatory) so concurrent cross-process writers/readers over one
        # file-backed $MEMORY_STORE don't block — e.g. MCP recall-path writes alongside the
        # Daydreamer. Harmless no-op for the in-memory default (PRAGMA returns 'memory', not 'wal');
        # it only takes effect for a file-backed DB, which is the path the plugin/harness uses.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.row_factory = sqlite3.Row  # access columns by name, not fragile indices
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS items ("
            "item_id TEXT PRIMARY KEY, content TEXT, timestamp REAL, relevancy REAL, "
            "session_id TEXT, source TEXT, tags TEXT, tokens INTEGER, version INTEGER, "
            "metadata TEXT, vector TEXT)"
        )
        self._conn.commit()

    def _embed_text(self, text: str, input_type: str) -> list:
        """Embed ``text``, passing ``input_type`` only if the embedder accepts it.

        Query/document asymmetry without breaking the legacy one-arg seam: a
        query/document-aware embedder gets ``input_type`` ('document' | 'query'); a
        legacy ``text -> vector`` callable is called positionally as before.
        """
        if self._embed_accepts_input_type:
            return self._embed(text, input_type=input_type)
        return self._embed(text)

    # -- MemoryStore protocol ----------------------------------------------
    def write(self, item: MemoryItem) -> None:
        tokens = item.tokens  # don't mutate the caller's item; store a local value
        if tokens <= 0 and item.content:
            tokens = _estimate_tokens(item.content)
        self._conn.execute(
            "INSERT OR REPLACE INTO items (item_id, content, timestamp, relevancy, "
            "session_id, source, tags, tokens, version, metadata, vector) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (item.item_id, item.content, item.timestamp, item.relevancy,
             item.session_id, item.source, json.dumps(list(item.tags)),
             tokens, item.version, json.dumps(item.metadata or {}),
             json.dumps(self._embed_text(item.content, "document"))),
        )
        self._conn.commit()

    def get(self, item_id: str) -> Optional[MemoryItem]:
        row = self._conn.execute(
            f"SELECT {_ITEM_COLS} FROM items WHERE item_id = ?", (item_id,)
        ).fetchone()
        return self._row_to_item(row) if row else None

    def search(self, query: str, *, k: int = 5, as_of: Optional[float] = None,
               **kwargs: Any) -> list:
        """Top-``k`` items by cosine similarity, best first, honoring ``as_of``.

        Brute-force over all rows (fine at offline/test scale); the paid path swaps in
        an ANN index. Ranking ties break by relevancy, timestamp, id — matching the
        other backends, so cross-backend comparisons stay fair.
        """
        q = self._embed_text(query, "query")
        if not any(q):  # empty / sub-n-gram query -> no signal -> no results (cf. MarkdownStore)
            return []
        rows = self._conn.execute(f"SELECT {_ITEM_COLS}, vector FROM items").fetchall()
        scored: list = []
        for row in rows:
            ts = row["timestamp"]
            if as_of is not None and ts is not None and ts > as_of:
                continue
            score = _cosine(q, json.loads(row["vector"]))
            scored.append((score, self._row_to_item(row)))
        scored.sort(key=lambda si: (-si[0], -si[1].relevancy, -si[1].timestamp, si[1].item_id))
        return [RetrievedItem(item=it, score=sc, rank=r)
                for r, (sc, it) in enumerate(scored[: max(0, k)])]

    def all(self) -> list:
        rows = self._conn.execute(
            f"SELECT {_ITEM_COLS} FROM items ORDER BY rowid"
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    # -- helpers -----------------------------------------------------------
    def _row_to_item(self, row) -> MemoryItem:
        return MemoryItem(
            item_id=row["item_id"], content=row["content"], timestamp=row["timestamp"],
            relevancy=row["relevancy"], session_id=row["session_id"], source=row["source"],
            tags=json.loads(row["tags"] or "[]"), tokens=row["tokens"],
            version=row["version"], metadata=json.loads(row["metadata"] or "{}"),
        )

    def close(self) -> None:
        """Close the underlying sqlite connection."""
        self._conn.close()

    def __enter__(self) -> "SqliteVectorStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


_ITEM_COLS = ("item_id, content, timestamp, relevancy, session_id, source, "
              "tags, tokens, version, metadata")

__all__ = ["SqliteVectorStore"]
