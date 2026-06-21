"""The Orchestrator seam — the only memory interface the plugin calls.

In the system diagram the conscious path is **Plugin → Session → Orchestrator
(route · rank · dedup) ↔ Memory**. The Orchestrator is the waist: it owns routing,
ranking, and dedup, and performs every read/write to the stores itself. The plugin
is a *client* of it (ADR-storage-001, ADR-harness-002) — it never sees a store,
never constructs a backend, and never touches Memory directly.

This module defines the interface the plugin builds against:

* :class:`Orchestrator` — a ``typing.Protocol``: ``recall(query, k)`` returns ranked
  hits; ``remember(content, tags)`` returns the new/merged memory id.
* :class:`NullOrchestrator` — the fail-open implementation used whenever the real
  Orchestrator isn't wired yet: ``recall`` returns empty, ``remember`` no-ops
  (ADR-harness-006). It lets the plugin install and run in a live session before the
  storage workstream's Orchestrator lands.
* :func:`make_orchestrator` — resolves the Orchestrator to use from the environment,
  falling back to :class:`NullOrchestrator` so a missing/incomplete backend degrades
  rather than breaks.

The real Orchestrator (the storage workstream's ``MemoryFramework``, route · rank ·
dedup over the backends) drops in behind :class:`Orchestrator` with no plugin change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@dataclass(slots=True)
class Hit:
    """One recalled memory, flattened for an adapter/tool response.

    Mirrors the fields the MCP ``recall`` tool returns (ADR-harness-002):
    ``id``, ``content``, ``score``, ``tokens`` — plus ``rank`` for ordering. Kept
    separate from the eval package's ``RetrievedItem`` so the plugin's public
    response shape doesn't depend on an internal type.
    """

    id: str
    content: str
    score: float
    tokens: int
    rank: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "content": self.content,
            "score": self.score,
            "tokens": self.tokens,
            "rank": self.rank,
        }


@runtime_checkable
class Orchestrator(Protocol):
    """The route · rank · dedup waist the plugin calls. Storage owns the impl."""

    def recall(self, query: str, *, k: int = 5, as_of: Optional[float] = None) -> list[Hit]:
        """Return up to ``k`` ranked hits for ``query`` (best first)."""
        ...

    def remember(
        self,
        content: str,
        *,
        tags: Optional[list[str]] = None,
        timestamp: float = 0.0,
    ) -> str:
        """Persist ``content`` (route · dedup · version) and return its memory id."""
        ...


class NullOrchestrator:
    """Fail-open Orchestrator: recall → empty, remember → no-op (ADR-harness-006).

    Used whenever no real Orchestrator is configured or one can't be constructed,
    so the plugin never breaks a session waiting on the storage workstream. The
    ``reason`` explains why it's inactive (surfaced in the events stream by callers).
    """

    def __init__(self, reason: str = "no orchestrator configured") -> None:
        self.reason = reason

    def recall(self, query: str, *, k: int = 5, as_of: Optional[float] = None) -> list[Hit]:
        return []

    def remember(
        self,
        content: str,
        *,
        tags: Optional[list[str]] = None,
        timestamp: float = 0.0,
    ) -> str:
        return ""


def make_orchestrator(env: Optional[dict[str, str]] = None) -> Orchestrator:
    """Resolve the Orchestrator to use, degrading to :class:`NullOrchestrator`.

    Resolution is environment-driven and fail-open by design:

    * If ``$MEMORY_STORE`` is unset, there is nowhere to store memory →
      :class:`NullOrchestrator`.
    * Otherwise attempt to construct the storage workstream's real Orchestrator over
      that store. Until that interface exists (or if its import/construction fails),
      fall back to :class:`NullOrchestrator` — the plugin stays usable either way.

    The construction of the real backend is intentionally lazy and guarded so the
    plugin imports cleanly with no storage dependency present.
    """
    import os

    env = os.environ if env is None else env
    store_path = env.get("MEMORY_STORE")
    if not store_path:
        return NullOrchestrator("MEMORY_STORE not set")

    try:
        return _build_real_orchestrator(store_path, env)
    except Exception as exc:  # pragma: no cover - exercised once the backend exists
        return NullOrchestrator(f"orchestrator unavailable: {exc}")


def _build_real_orchestrator(store_path: str, env: dict[str, str]) -> Orchestrator:
    """Construct the storage workstream's Orchestrator over ``store_path``.

    The real Orchestrator (route · rank · dedup over the backends) is owned by the
    storage workstream and is not yet present in the tree. Raising here is the
    expected, handled path: :func:`make_orchestrator` turns it into a fail-open
    :class:`NullOrchestrator`. When the storage Orchestrator lands, this is where it
    is wired — no other plugin file changes.
    """
    raise NotImplementedError("storage Orchestrator (route·rank·dedup) not yet wired")


__all__ = ["Orchestrator", "NullOrchestrator", "Hit", "make_orchestrator"]
