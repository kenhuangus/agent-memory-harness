"""OpenCode memory framework — owner: Keith (@kmazanec).

This package is where the **real agent** lives: Keith wraps the OpenCode loop as
an :class:`~memeval.agent.AgentAdapter` and wires it to the components the other
captains own, all behind the frozen contract (``protocols.py`` / ``schema.py``):

* **store + retrieve** — Brent's :mod:`memeval.stores` backends, dispatched by his
  :class:`memeval.router.Router`. The framework holds them behind the
  ``MemoryStore`` protocol, so OpenCode never imports a concrete backend.
* **dreaming** — Scott B.'s :class:`memeval.dreaming.DreamingWorker`, run between
  sessions/tasks to consolidate the shared store.

The eval-side seam already exists in :mod:`memeval.agent` (``AgentAdapter`` +
``run_agent``); this package is the concrete implementation that plugs into it.

Scaffold — the agent loop and the framework glue raise ``NotImplementedError``
until implemented. See :mod:`memeval.opencode.agent` and
:mod:`memeval.opencode.framework`.
"""

from __future__ import annotations

from .agent import OpenCodeAgent
from .framework import MemoryFramework

__all__ = ["OpenCodeAgent", "MemoryFramework"]
