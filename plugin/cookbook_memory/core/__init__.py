"""Harness-agnostic plugin core: the Orchestrator seam, recall/remember, events.

Everything in ``core`` is independent of any specific coding harness. The Claude
Code adapter (and future OpenCode/Codex adapters) wrap this core; the core itself
calls only the Orchestrator (route · rank · dedup) and never a store.
"""

from __future__ import annotations

from .config import Settings
from .events import EventStream, event
from .memory import Memory
from .orchestrator import Hit, NullOrchestrator, Orchestrator, make_orchestrator


def build_memory(
    *,
    store: str | None = None,
    session_id: str | None = None,
    k: int | None = None,
    env: dict[str, str] | None = None,
) -> Memory:
    """Construct a ready-to-use :class:`Memory` from the environment.

    This is the one entry point every adapter uses: it resolves settings
    (store-by-path), the Orchestrator (fail-open if absent), and the events stream,
    and wires them into a :class:`Memory`. The result is safe to use immediately —
    a missing backend degrades to no-ops rather than raising.
    """
    settings = Settings.from_env(env, store=store, session_id=session_id, k=k)
    orch = make_orchestrator(
        {"MEMORY_STORE": str(settings.store_path)} if settings.store_path else {}
    )
    events = EventStream(settings.events_path)
    return Memory(orch, events, session_id=settings.session_id, default_k=settings.default_k)


__all__ = [
    "Memory",
    "Orchestrator",
    "NullOrchestrator",
    "Hit",
    "make_orchestrator",
    "EventStream",
    "event",
    "Settings",
    "build_memory",
]
