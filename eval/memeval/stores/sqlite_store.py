"""SQLite + vectors backend — owner: Brent (@bgibson1618). Implements ``MemoryStore``.

v1 is **stdlib-only**: rows live in ``sqlite3``; each item is embedded by a
deterministic char-n-gram feature-hashing embedder and searched by brute-force
cosine. This keeps the offline/test path zero-dependency.

The paid/local upgrade changes nothing about the ``MemoryStore`` contract: inject a
real dense embedder via ``SqliteVectorStore(embed=...)`` (Voyage or local MiniLM; see
:mod:`memeval.stores.embedders`) and optionally request ``vector_index="sqlite_vec"``
for sqlite-vec ANN overfetch plus exact rerank. Offline similarity is fuzzy/lexical
(char-n-grams catch morphology the keyword store can't); true dense semantics arrive
with the real embedder.

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
import threading
from typing import Any, Callable, Iterator, Optional

from ..schema import MemoryItem, RetrievedItem

VECTOR_INDEX_BRUTE_FORCE = "brute_force"
VECTOR_INDEX_SQLITE_VEC = "sqlite_vec"
SQLITE_VEC_DIM = 384
SQLITE_VEC_ANN_OVERFETCH = 50
SQLITE_VEC_RECALL_AT_10_THRESHOLD = 0.98

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

    def __init__(
        self,
        path: str = ":memory:",
        *,
        embed: Optional[Embedder] = None,
        embed_model: Optional[str] = None,
        dim: int = 256,
        vector_index: str = VECTOR_INDEX_BRUTE_FORCE,
        ann_overfetch: int = SQLITE_VEC_ANN_OVERFETCH,
        exact_rerank: bool = True,
    ) -> None:
        self.path = str(path)
        self.embed_model = embed_model
        self._embed = embed or _HashingEmbedder(dim)
        self.requested_vector_index = vector_index
        self.vector_index = VECTOR_INDEX_BRUTE_FORCE
        self.vector_index_status = "brute-force exact"
        self.ann_overfetch = ann_overfetch
        self.exact_rerank = exact_rerank
        self._sqlite_vec: Any = None
        self._indexed_generation: Optional[int] = None
        # Detected once: does this embedder want the query/document ``input_type`` kwarg?
        self._embed_accepts_input_type = _embedder_accepts_input_type(self._embed)
        # Thread-safety (BACKEND_DURABILITY_AUDIT MED): the harness's own ThreadPoolExecutor
        # (run_agent workers>1, agent.py) hands ONE store to every worker. A default
        # thread-affine connection (check_same_thread=True) deterministically crashes with
        # sqlite3.ProgrammingError there. check_same_thread=False lets a single connection be
        # shared across threads, and ``_lock`` serializes EVERY access to it (check_same_thread
        # =False ALONE is unsafe — writes/searches would interleave on one connection and the
        # cursor state would corrupt). WAL still gives cross-PROCESS concurrency; the lock is
        # the intra-process guard.
        self._lock = threading.Lock()
        self._closed = False  # lifecycle flag — checked UNDER _lock so a write can't race close()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        # WAL journal mode (ADR-P2: mandatory) so concurrent cross-process writers/readers over one
        # file-backed $MEMORY_STORE don't block — e.g. MCP recall-path writes alongside the
        # Daydreamer. ENFORCED, not assumed: SQLite can fall back to another journal mode without
        # erroring (e.g. an unsupported filesystem), silently breaking the cross-process guarantee, so
        # we check the returned mode and fail loud for a file-backed DB. The in-memory default returns
        # 'memory' (a harmless no-op) — that is the only non-'wal' mode we accept.
        mode = self._conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(mode).lower() not in ("wal", "memory"):
            raise RuntimeError(
                f"SqliteVectorStore requires WAL for a file-backed DB (ADR-P2); "
                f"got journal_mode={mode!r} for path {self.path!r}"
            )
        self._conn.row_factory = sqlite3.Row  # access columns by name, not fragile indices
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS items ("
            "item_id TEXT PRIMARY KEY, content TEXT, timestamp REAL, relevancy REAL, "
            "session_id TEXT, source TEXT, tags TEXT, tokens INTEGER, version INTEGER, "
            "metadata TEXT, vector TEXT)"
        )
        if vector_index not in (VECTOR_INDEX_BRUTE_FORCE, VECTOR_INDEX_SQLITE_VEC):
            raise ValueError(f"unknown vector_index {vector_index!r}")
        if vector_index == VECTOR_INDEX_SQLITE_VEC:
            self._try_enable_sqlite_vec()
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
        # Embed OUTSIDE the lock (a real embedder can be slow / can block); only the DB
        # txn is serialized. Rolls back on a failed commit so a failure precisely at commit
        # cannot strand a partial txn on the shared connection for a later write to silently
        # commit — matching delete()'s hygiene (BACKEND_DURABILITY_AUDIT LOW).
        vector_list = self._embed_text(item.content, "document")
        vector = json.dumps(vector_list)
        with self._lock:
            self._raise_if_closed()  # under the lock: a concurrent close() can't null the conn mid-write
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO items (item_id, content, timestamp, relevancy, "
                    "session_id, source, tags, tokens, version, metadata, vector) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (item.item_id, item.content, item.timestamp, item.relevancy,
                     item.session_id, item.source, json.dumps(list(item.tags)),
                     tokens, item.version, json.dumps(item.metadata or {}), vector),
                )
                if self.vector_index == VECTOR_INDEX_SQLITE_VEC:
                    self._upsert_sqlite_vec_unlocked(
                        item.item_id, vector_list, item.timestamp
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def get(self, item_id: str) -> Optional[MemoryItem]:
        with self._lock:
            self._raise_if_closed()
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
        if self.vector_index == VECTOR_INDEX_SQLITE_VEC:
            return self._search_sqlite_vec(q, k=k, as_of=as_of)
        return self._search_brute_force(q, k=k, as_of=as_of)

    def _search_brute_force(
        self, q: list, *, k: int, as_of: Optional[float] = None
    ) -> list:
        with self._lock:  # serialize the connection access; the cosine scan is pure-Python
            self._raise_if_closed()
            rows = self._conn.execute(f"SELECT {_ITEM_COLS}, vector FROM items").fetchall()
        return self._rank_exact(q, rows, k=k, as_of=as_of)

    def _search_sqlite_vec(
        self, q: list, *, k: int, as_of: Optional[float] = None
    ) -> list:
        fetch = max(max(0, k), int(self.ann_overfetch))
        if fetch <= 0:
            return []
        query = self._sqlite_vec_payload(q)
        with self._lock:
            self._raise_if_closed()
            self._ensure_sqlite_vec_coherent_unlocked()
            if as_of is None:
                rows = self._conn.execute(
                    f"""
                    WITH ann AS (
                        SELECT rowid AS vec_rowid, distance
                        FROM item_vectors
                        WHERE embedding MATCH ? AND k = ?
                        ORDER BY distance ASC
                    )
                    SELECT {_ITEM_COLS_I}, i.vector, ann.distance
                    FROM ann
                    JOIN item_vec_ids m ON m.vec_rowid = ann.vec_rowid
                    JOIN items i ON i.item_id = m.item_id
                    ORDER BY ann.distance ASC
                    """,
                    (query, fetch),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    f"""
                    WITH ann AS (
                        SELECT rowid AS vec_rowid, distance
                        FROM item_vectors
                        WHERE embedding MATCH ? AND k = ? AND timestamp <= ?
                        ORDER BY distance ASC
                    )
                    SELECT {_ITEM_COLS_I}, i.vector, ann.distance
                    FROM ann
                    JOIN item_vec_ids m ON m.vec_rowid = ann.vec_rowid
                    JOIN items i ON i.item_id = m.item_id
                    ORDER BY ann.distance ASC
                    """,
                    (query, fetch, as_of),
                ).fetchall()
        if self.exact_rerank:
            return self._rank_exact(q, rows, k=k, as_of=as_of)
        out: list = []
        for rank, row in enumerate(rows[: max(0, k)]):
            out.append(
                RetrievedItem(
                    item=self._row_to_item(row),
                    score=1.0 - float(row["distance"]),
                    rank=rank,
                )
            )
        return out

    def _rank_exact(
        self, q: list, rows: list, *, k: int, as_of: Optional[float] = None
    ) -> list:
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
        with self._lock:
            self._raise_if_closed()
            rows = self._conn.execute(
                f"SELECT {_ITEM_COLS} FROM items ORDER BY rowid"
            ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def iter_pages(self, *, page_size: int = 1000) -> Iterator[list]:
        """ADR-dreaming-028 §2 PR #2g — stream items in pages of up to
        ``page_size``. Avoids materializing the full ``items`` table in
        Python on large stores.

        Uses ``cursor.fetchmany(page_size)`` over a rowid-ordered SELECT so
        pages arrive in the same order ``all()`` returns; SQLite never seeks.
        ``self._lock`` is held for the duration of iteration — consumers
        should not call other store operations from inside the loop. The
        dream worker (the intended caller) runs under a basedir flock so
        no concurrent dream invocations compete; within a single process
        the lock serializes connection access the same way ``all()`` does.
        """
        if page_size <= 0:
            raise ValueError(f"page_size must be > 0, got {page_size}")
        with self._lock:
            self._raise_if_closed()
            cursor = self._conn.execute(
                f"SELECT {_ITEM_COLS} FROM items ORDER BY rowid"
            )
            while True:
                rows = cursor.fetchmany(page_size)
                if not rows:
                    break
                yield [self._row_to_item(r) for r in rows]

    def delete(self, item_id: str) -> bool:
        """Delete the row for ``item_id``; return ``True`` if a row was removed (idempotent). Rolls back
        on failure so a partial change never lands."""
        with self._lock:
            self._raise_if_closed()
            try:
                vec_rowid = None
                if self.vector_index == VECTOR_INDEX_SQLITE_VEC:
                    row = self._conn.execute(
                        "SELECT vec_rowid FROM item_vec_ids WHERE item_id = ?",
                        (item_id,),
                    ).fetchone()
                    vec_rowid = row["vec_rowid"] if row else None
                cur = self._conn.execute("DELETE FROM items WHERE item_id = ?", (item_id,))
                if cur.rowcount > 0 and self.vector_index == VECTOR_INDEX_SQLITE_VEC:
                    if vec_rowid is not None:
                        self._conn.execute(
                            "DELETE FROM item_vectors WHERE rowid = ?", (vec_rowid,)
                        )
                    self._conn.execute(
                        "DELETE FROM item_vec_ids WHERE item_id = ?", (item_id,)
                    )
                    self._bump_sqlite_vec_generation_unlocked()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            return cur.rowcount > 0

    # -- helpers -----------------------------------------------------------
    def _try_enable_sqlite_vec(self) -> None:
        try:
            import sqlite_vec
        except Exception as exc:
            self.vector_index_status = f"sqlite-vec unavailable: {exc}"
            return
        try:
            if hasattr(self._conn, "enable_load_extension"):
                self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            if hasattr(self._conn, "enable_load_extension"):
                self._conn.enable_load_extension(False)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS item_vec_ids ("
                "item_id TEXT PRIMARY KEY, vec_rowid INTEGER NOT NULL UNIQUE)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS vector_rowid_seq ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), next_rowid INTEGER NOT NULL)"
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO vector_rowid_seq (id, next_rowid) VALUES (1, 1)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS vector_meta ("
                "name TEXT PRIMARY KEY, value INTEGER NOT NULL)"
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO vector_meta (name, value) VALUES ('generation', 0)"
            )
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS item_vectors USING vec0("
                f"embedding float[{SQLITE_VEC_DIM}] distance_metric=cosine, "
                "timestamp float)"
            )
            self._sqlite_vec = sqlite_vec
            self.vector_index = VECTOR_INDEX_SQLITE_VEC
            self.vector_index_status = "sqlite-vec active"
            self._rebuild_sqlite_vec_if_needed_unlocked()
            self._indexed_generation = self._read_sqlite_vec_generation_unlocked()
        except Exception as exc:
            self._conn.rollback()
            self._sqlite_vec = None
            self.vector_index = VECTOR_INDEX_BRUTE_FORCE
            self.vector_index_status = f"sqlite-vec fallback to brute force: {exc}"
            try:
                if hasattr(self._conn, "enable_load_extension"):
                    self._conn.enable_load_extension(False)
            except Exception:
                pass

    def _sqlite_vec_payload(self, vector: list) -> Any:
        values = [float(x) for x in vector]
        if len(values) != SQLITE_VEC_DIM:
            raise ValueError(
                f"sqlite-vec index requires {SQLITE_VEC_DIM}-dim vectors, got {len(values)}"
            )
        serialize = getattr(self._sqlite_vec, "serialize_float32", None)
        return serialize(values) if callable(serialize) else json.dumps(values)

    def _next_vec_rowid_unlocked(self) -> int:
        row = self._conn.execute(
            "SELECT next_rowid FROM vector_rowid_seq WHERE id = 1"
        ).fetchone()
        vec_rowid = int(row["next_rowid"])
        self._conn.execute(
            "UPDATE vector_rowid_seq SET next_rowid = ? WHERE id = 1",
            (vec_rowid + 1,),
        )
        return vec_rowid

    def _upsert_sqlite_vec_unlocked(
        self, item_id: str, vector: list, timestamp: Optional[float]
    ) -> None:
        row = self._conn.execute(
            "SELECT vec_rowid FROM item_vec_ids WHERE item_id = ?", (item_id,)
        ).fetchone()
        if row:
            vec_rowid = int(row["vec_rowid"])
        else:
            vec_rowid = self._next_vec_rowid_unlocked()
            self._conn.execute(
                "INSERT INTO item_vec_ids (item_id, vec_rowid) VALUES (?, ?)",
                (item_id, vec_rowid),
            )
        self._conn.execute("DELETE FROM item_vectors WHERE rowid = ?", (vec_rowid,))
        self._conn.execute(
            "INSERT INTO item_vectors (rowid, embedding, timestamp) VALUES (?, ?, ?)",
            (vec_rowid, self._sqlite_vec_payload(vector), timestamp),
        )
        self._bump_sqlite_vec_generation_unlocked()

    def _rebuild_sqlite_vec_if_needed_unlocked(self) -> None:
        item_count = self._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        map_count = self._conn.execute("SELECT COUNT(*) FROM item_vec_ids").fetchone()[0]
        if item_count == map_count:
            return
        self._conn.execute("DELETE FROM item_vectors")
        self._conn.execute("DELETE FROM item_vec_ids")
        self._conn.execute("UPDATE vector_rowid_seq SET next_rowid = 1 WHERE id = 1")
        rows = self._conn.execute(
            "SELECT item_id, timestamp, vector FROM items ORDER BY rowid"
        ).fetchall()
        for row in rows:
            self._upsert_sqlite_vec_unlocked(
                row["item_id"], json.loads(row["vector"]), row["timestamp"]
            )

    def _read_sqlite_vec_generation_unlocked(self) -> int:
        row = self._conn.execute(
            "SELECT value FROM vector_meta WHERE name = 'generation'"
        ).fetchone()
        return int(row["value"]) if row else 0

    def _bump_sqlite_vec_generation_unlocked(self) -> None:
        self._conn.execute(
            "UPDATE vector_meta SET value = value + 1 WHERE name = 'generation'"
        )
        self._indexed_generation = self._read_sqlite_vec_generation_unlocked()

    def _ensure_sqlite_vec_coherent_unlocked(self) -> None:
        current = self._read_sqlite_vec_generation_unlocked()
        if self._indexed_generation != current:
            self._indexed_generation = current

    def _raise_if_closed(self) -> None:
        """Fail loud if the store is closed. MUST be called while holding ``_lock`` — so a
        write that passed an un-held check can't then race a concurrent ``close()`` and mutate
        a closed/null connection. Does NOT take the lock itself (no re-entrancy)."""
        if self._closed:
            raise RuntimeError("operation on a closed SqliteVectorStore")

    def _row_to_item(self, row) -> MemoryItem:
        return MemoryItem(
            item_id=row["item_id"], content=row["content"], timestamp=row["timestamp"],
            relevancy=row["relevancy"], session_id=row["session_id"], source=row["source"],
            tags=json.loads(row["tags"] or "[]"), tokens=row["tokens"],
            version=row["version"], metadata=json.loads(row["metadata"] or "{}"),
        )

    def close(self) -> None:
        """Close the underlying sqlite connection. Acquires ``_lock`` so it can't close the
        connection out from under an in-flight write/search (which hold the same lock), then
        marks the store closed so a later operation fails loud instead of touching a closed
        connection. Idempotent. Must NOT call a lock-taking method (no deadlock)."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()

    def __enter__(self) -> "SqliteVectorStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


_ITEM_COLS = ("item_id, content, timestamp, relevancy, session_id, source, "
              "tags, tokens, version, metadata")
# i.-qualified variant for the sqlite-vec JOIN, where `item_id` is ambiguous
# between `items` and the `item_vec_ids` map. Result column names are unchanged.
_ITEM_COLS_I = ("i.item_id, i.content, i.timestamp, i.relevancy, i.session_id, i.source, "
                "i.tags, i.tokens, i.version, i.metadata")

__all__ = [
    "SqliteVectorStore",
    "SQLITE_VEC_ANN_OVERFETCH",
    "SQLITE_VEC_DIM",
    "SQLITE_VEC_RECALL_AT_10_THRESHOLD",
    "VECTOR_INDEX_BRUTE_FORCE",
    "VECTOR_INDEX_SQLITE_VEC",
]
