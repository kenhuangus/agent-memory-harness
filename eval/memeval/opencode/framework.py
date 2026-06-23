"""OpenCode memory framework — the glue layer. Owner: Keith (@kmazanec). Scaffold.

The :class:`MemoryFramework` is the single object OpenCode talks to for memory.
It hides *which* backend answers a query and *when* consolidation happens, so the
agent loop only sees ``remember`` / ``recall``. It is built from the components
the other captains own — all referenced through the frozen protocols, never their
concrete classes:

* ``router``  — Brent's :class:`memeval.router.Router` (query -> one backend).
* ``backends`` — Brent's :mod:`memeval.stores` (graph / vectors / markdown),
  each a ``MemoryStore``.
* ``dreamer`` — Scott B.'s :class:`memeval.dreaming.DreamingWorker`.

The framework itself **is a** ``MemoryStore`` (it implements ``write`` / ``get`` /
``search`` / ``all`` / ``delete``), so it can be handed straight to
``memeval.agent.run_agent(..., store=framework)`` as the shared store. That is the
integration point: Keith's framework, backed by Brent's storage, evaluated by
Ken's harness, cleaned by Scott's dreaming.

TODO(keith):
  1) ``write`` -> persistence policy (what/when/where) then fan to the right backend;
  2) ``search`` -> ``router.route(query)`` then ``backend.search`` (respect ``as_of``);
  3) ``maybe_dream`` -> trigger ``dreamer.run`` on a cadence (per N writes / per session);
  4) keep it stdlib-only at import; lazy-import any heavy backend dep.
"""

from __future__ import annotations

from typing import Any, Optional

from ..protocols import MemoryStore
from ..schema import MemoryItem, RetrievedItem


class MemoryFramework:
    """OpenCode's memory facade: routes reads/writes to Brent's backends and runs
    Scott's dreaming. Implements ``MemoryStore`` so it drops into ``run_agent``. (stub)

    Parameters mirror the contract seams so nothing here depends on a concrete
    implementation: pass a ``router`` (Brent), the ``backends`` map it routes over
    (Brent), and a ``dreamer`` (Scott). All optional so the scaffold imports with
    zero wiring.
    """

    def __init__(
        self,
        *,
        router: Any = None,            # memeval.router.Router (Brent)
        backends: Optional[dict[str, MemoryStore]] = None,  # memeval.stores (Brent)
        dreamer: Any = None,           # memeval.dreaming.DreamingWorker (Scott B.)
        dream_every: int = 0,          # 0 = never; else run dreaming every N writes
    ) -> None:
        self.router = router
        self.backends = backends or {}
        self.dreamer = dreamer
        self.dream_every = dream_every
        self._writes_since_dream = 0

    # -- MemoryStore protocol (write side) -------------------------------- #
    def write(self, item: MemoryItem) -> None:
        """Persist ``item`` per the write policy, then to the chosen backend. (stub)"""
        raise NotImplementedError(
            "MemoryFramework.write — TODO(keith): write policy + fan to Brent's backend"
        )

    def get(self, item_id: str) -> Optional[MemoryItem]:
        raise NotImplementedError("MemoryFramework.get — TODO(keith)")

    # -- MemoryStore protocol (read side) --------------------------------- #
    def search(self, query: str, k: int = 5, *, as_of: Any = None) -> list[RetrievedItem]:
        """Route ``query`` to one backend (Brent's Router) and search it. (stub)"""
        raise NotImplementedError(
            "MemoryFramework.search — TODO(keith): router.route(query) -> backend.search"
        )

    def all(self) -> list[MemoryItem]:
        raise NotImplementedError("MemoryFramework.all — TODO(keith)")

    def delete(self, item_id: str) -> bool:
        """Delete ``item_id`` via the Router/RouterStore. (stub)"""
        raise NotImplementedError(
            "MemoryFramework.delete — TODO(keith): RouterStore.delete(item_id)"
        )

    # -- consolidation (Scott B.'s dreaming) ------------------------------ #
    def maybe_dream(self, **kwargs: Any) -> Optional[dict]:
        """Run a dreaming pass when the cadence is hit; return its summary. (stub)"""
        raise NotImplementedError(
            "MemoryFramework.maybe_dream — TODO(keith): trigger Scott's DreamingWorker.run"
        )


__all__ = ["MemoryFramework"]
