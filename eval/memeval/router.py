"""Intelligent router — owner: Brent (@bgibson1618).

Classifies a query and dispatches it to the **single best** backend instead of
fanning out: relationship queries → graph, semantic → vectors, literal keyword →
markdown. Rule-based first, with a learned-classifier upgrade path (same
signature, no caller change). See ``architecture.md``.

Scaffold — :meth:`Router.route` raises ``NotImplementedError`` until implemented.
"""

from __future__ import annotations

from typing import Any, Optional

from .protocols import MemoryStore


class Router:
    """Routes a query to one of the registered :class:`MemoryStore` backends. (stub)

    ``backends`` maps a name (e.g. ``"graph"`` / ``"vectors"`` / ``"markdown"``)
    to a concrete store. The retrieval orchestrator calls :meth:`route` then runs
    ``search`` on just the chosen backend.
    """

    def __init__(self, backends: Optional[dict[str, MemoryStore]] = None) -> None:
        self.backends = backends or {}

    def route(self, query: str, **kwargs: Any) -> MemoryStore:
        raise NotImplementedError(
            "Router.route — TODO(brent): rule-based dispatch (graph/vectors/markdown)"
        )


__all__ = ["Router"]
