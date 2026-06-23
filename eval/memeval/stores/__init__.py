"""Storage backends — owner: **Brent** (@bgibson1618).

Each backend implements :class:`memeval.protocols.MemoryStore`
(``write`` / ``get`` / ``search`` / ``all`` / ``delete``) so the harness, router, and
dreaming worker use them interchangeably. The reference offline implementation
is :class:`memeval.harness.InMemoryStore`; the real backends here are:

* :class:`markdown_store.MarkdownStore`   — OKF-native + inverted keyword index (literal recall)
* :class:`sqlite_store.SqliteVectorStore` — ``sqlite3`` + a char-n-gram hashing embedder + brute-force
  cosine (v1, stdlib); a real dense embedder (Voyage/bge) injects via ``embed=`` and an ANN index
  (HNSW/FAISS) is a deferred paid-path upgrade
* :class:`graph_store.GraphStore`         — in-memory OKF-link graph, seed-then-traverse (v1, stdlib);
  a typed-edge graph DB (Neo4j) is a deferred paid-path seam (``uri=``)

Heavy deps are lazy-imported inside the methods that need them, so importing this package stays
stdlib-only (offline path unaffected). All three backends are **implemented** (``write`` / ``get`` /
``search`` / ``all`` / ``delete``); the paid-path upgrades (real embeddings / ANN, Neo4j) inject behind seams and
never touch the default offline path.
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
