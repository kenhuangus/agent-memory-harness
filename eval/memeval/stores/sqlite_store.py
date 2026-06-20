"""SQLite + vectors backend — owner: Brent (@bgibson1618). Implements ``MemoryStore``.

v1 is **stdlib-only**: rows live in ``sqlite3``; each item is embedded by a
deterministic char-n-gram feature-hashing embedder and searched by brute-force
cosine. This keeps the offline/test path zero-dependency.

The paid-path upgrade changes nothing about the ``MemoryStore`` contract: inject a
real dense embedder via ``SqliteVectorStore(embed=...)`` (Voyage ``voyage-3-large`` /
``bge-m3``, PRD §7.1) and later swap brute-force for an ANN index (HNSW/FAISS).
Offline similarity is fuzzy/lexical (char-n-grams catch morphology the keyword store
can't); true dense semantics arrive with the real embedder.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from typing import Any, Callable, Optional

from ..schema import MemoryItem, RetrievedItem

Embedder = Callable[[str], list]


class _HashingEmbedder:
    """Deterministic char-n-gram feature-hashing embedder (stdlib, offline).

    Hashes each character n-gram to a signed bucket in a fixed-dim vector and
    L2-normalizes. Gives fuzzy/morphological similarity with no model or dependency.
    Real dense embeddings are injected via ``SqliteVectorStore(embed=...)``.
    """

    def __init__(self, dim: int = 256, n: int = 3) -> None:
        self.dim, self.n = dim, n

    def __call__(self, text: str) -> list:
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
        return max(1, len((content or "").split()))


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
    """

    def __init__(self, path: str = ":memory:", *, embed: Optional[Embedder] = None,
                 embed_model: Optional[str] = None, dim: int = 256) -> None:
        self.path = str(path)
        self.embed_model = embed_model
        self._embed = embed or _HashingEmbedder(dim)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS items ("
            "item_id TEXT PRIMARY KEY, content TEXT, timestamp REAL, relevancy REAL, "
            "session_id TEXT, source TEXT, tags TEXT, tokens INTEGER, version INTEGER, "
            "metadata TEXT, vector TEXT)"
        )
        self._conn.commit()

    # -- MemoryStore protocol ----------------------------------------------
    def write(self, item: MemoryItem) -> None:
        if item.tokens <= 0 and item.content:
            item.tokens = _estimate_tokens(item.content)
        self._conn.execute(
            "INSERT OR REPLACE INTO items (item_id, content, timestamp, relevancy, "
            "session_id, source, tags, tokens, version, metadata, vector) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (item.item_id, item.content, item.timestamp, item.relevancy,
             item.session_id, item.source, json.dumps(list(item.tags)),
             item.tokens, item.version, json.dumps(item.metadata or {}),
             json.dumps(self._embed(item.content))),
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
        q = self._embed(query)
        if not any(q):  # empty / sub-n-gram query -> no signal -> no results (cf. MarkdownStore)
            return []
        rows = self._conn.execute(f"SELECT {_ITEM_COLS}, vector FROM items").fetchall()
        scored: list = []
        for row in rows:
            ts = row[2]
            if as_of is not None and ts is not None and ts > as_of:
                continue
            score = _cosine(q, json.loads(row[10]))
            scored.append((score, self._row_to_item(row[:10])))
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
            item_id=row[0], content=row[1], timestamp=row[2], relevancy=row[3],
            session_id=row[4], source=row[5], tags=json.loads(row[6] or "[]"),
            tokens=row[7], version=row[8], metadata=json.loads(row[9] or "{}"),
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
