"""Graph-store backend — owner: Brent. Implements ``MemoryStore``.

TODO(brent): store memories as nodes and their relationships/contradictions as
typed edges; ``search`` traverses from a query node along indexed edges bounded
by depth or a relevancy threshold. Lazily import the graph driver (e.g. Neo4j)
inside the methods; importing this module must stay stdlib-only.
"""

from __future__ import annotations

from typing import Any, Optional

from ..schema import MemoryItem, RetrievedItem


class GraphStore:
    """Graph-database MemoryStore with a typed traversal index. (stub)"""

    def __init__(self, uri: Optional[str] = None, **kwargs: Any) -> None:
        self.uri = uri
        self.config = kwargs

    def write(self, item: MemoryItem) -> None:
        raise NotImplementedError("GraphStore.write — TODO(brent)")

    def get(self, item_id: str) -> Optional[MemoryItem]:
        raise NotImplementedError("GraphStore.get — TODO(brent)")

    def search(
        self, query: str, *, k: int = 5, as_of: Optional[float] = None, **kwargs: Any
    ) -> list[RetrievedItem]:
        raise NotImplementedError("GraphStore.search — TODO(brent)")

    def all(self) -> list[MemoryItem]:
        raise NotImplementedError("GraphStore.all — TODO(brent)")


__all__ = ["GraphStore"]
