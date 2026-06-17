"""Storage backends — owner: **Brent** (@bgibson1618).

Each backend implements :class:`memeval.protocols.MemoryStore`
(``write`` / ``get`` / ``search`` / ``all``) so the harness, router, and
dreaming worker use them interchangeably. The reference offline implementation
is :class:`memeval.harness.InMemoryStore`; the real backends here are:

* :class:`markdown_store.MarkdownStore`   — inverted keyword index
* :class:`sqlite_store.SqliteVectorStore` — dense ANN (HNSW / FAISS)
* :class:`graph_store.GraphStore`         — typed traversal (Neo4j)

Heavy deps are lazy-imported inside the methods that need them, so importing
this package stays stdlib-only (offline path unaffected). These are scaffolds —
methods raise :class:`NotImplementedError` until implemented.
"""

from __future__ import annotations

from typing import Any

__all__ = ["MarkdownStore", "SqliteVectorStore", "GraphStore"]


def __getattr__(name: str) -> Any:  # lazy re-export; keeps package import cheap
    if name == "MarkdownStore":
        from .markdown_store import MarkdownStore
        return MarkdownStore
    if name == "SqliteVectorStore":
        from .sqlite_store import SqliteVectorStore
        return SqliteVectorStore
    if name == "GraphStore":
        from .graph_store import GraphStore
        return GraphStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
