"""SQLite + vectors backend — owner: Brent. Implements ``MemoryStore``.

TODO(brent): embed each item on write, store the row in SQLite and the vector in
an HNSW / FAISS index; ``search`` embeds the query, runs ANN, hydrates rows by id.
Lazily import the heavy deps (``sqlite3`` is stdlib; ``numpy`` / ``faiss`` /
embedding model imported INSIDE the methods) so importing this module stays
dependency-free.
"""

from __future__ import annotations

from typing import Any, Optional

from ..schema import MemoryItem, RetrievedItem


class SqliteVectorStore:
    """SQLite-backed MemoryStore with a dense ANN (HNSW/FAISS) index. (stub)"""

    def __init__(self, path: str = ":memory:", *, embed_model: Optional[str] = None) -> None:
        self.path = path
        self.embed_model = embed_model

    def write(self, item: MemoryItem) -> None:
        raise NotImplementedError("SqliteVectorStore.write — TODO(brent)")

    def get(self, item_id: str) -> Optional[MemoryItem]:
        raise NotImplementedError("SqliteVectorStore.get — TODO(brent)")

    def search(
        self, query: str, *, k: int = 5, as_of: Optional[float] = None, **kwargs: Any
    ) -> list[RetrievedItem]:
        raise NotImplementedError("SqliteVectorStore.search — TODO(brent)")

    def all(self) -> list[MemoryItem]:
        raise NotImplementedError("SqliteVectorStore.all — TODO(brent)")


__all__ = ["SqliteVectorStore"]
