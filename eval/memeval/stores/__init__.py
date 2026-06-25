"""Storage backends — owner: **Brent** (@bgibson1618).

Each backend implements :class:`memeval.protocols.MemoryStore`
(``write`` / ``get`` / ``search`` / ``all`` / ``delete``) so the harness, router, and
dreaming worker use them interchangeably. The reference offline implementation
is :class:`memeval.harness.InMemoryStore`; the real backends here are:

* :class:`markdown_store.MarkdownStore`   — OKF-native + inverted keyword index (literal recall)
* :class:`sqlite_store.SqliteVectorStore` — ``sqlite3`` + a char-n-gram hashing embedder + brute-force
  cosine (v1, stdlib); a real dense embedder (Voyage/MiniLM) injects via ``embed=`` and the
  opt-in ``vector_index="sqlite_vec"`` path uses sqlite-vec ANN with exact rerank
* :class:`graph_store.GraphStore`         — in-memory OKF-link graph, seed-then-traverse (v1, stdlib);
  a typed-edge graph DB (Neo4j) is the paid-path seam (``uri=``)
* :class:`neo4j_store.Neo4jGraphStore`    — the ``uri=`` upgrade of ``GraphStore``: a typed-edge graph DB
  over the Neo4j Bolt driver. **Phase A** is a parity FLOOR — ``search`` delegates scoring/BFS/tie-break
  to a transient in-memory ``GraphStore`` for EXACT id+order parity. ``neo4j`` is imported lazily inside
  ``connect()`` only (never at module load); a set ``uri`` with no driver fails loud.

Heavy deps are lazy-imported inside the methods that need them, so importing this package stays
stdlib-only (offline path unaffected). All backends are **implemented** (``write`` / ``get`` /
``search`` / ``all`` / ``delete``); the paid-path upgrades (real embeddings / ANN, Neo4j) inject behind seams and
never touch the default offline path.
"""

from __future__ import annotations

from typing import Any

__all__ = ["MarkdownStore", "SqliteVectorStore", "GraphStore", "Neo4jGraphStore"]


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
    if name == "Neo4jGraphStore":
        # The paid-path graph-DB seam (the uri= upgrade of GraphStore). Lazy, like the others — and
        # neo4j_store itself imports neo4j only inside connect(), so this stays stdlib-only at package
        # import (offline/CI path unaffected; importing the package never pulls in neo4j).
        from .neo4j_store import Neo4jGraphStore
        return Neo4jGraphStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
