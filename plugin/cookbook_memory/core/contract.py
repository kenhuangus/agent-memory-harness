"""The single import edge between the plugin and the memory engine it builds on.

The plugin reaches the frozen data model (``MemoryItem``, ``RetrievedItem``), the
``MemoryStore`` protocol, the ``Router`` (route · rank dispatch), and the concrete
store backends through this module alone. Every other plugin module imports these
names from here, never from the engine's source package directly — so the plugin
depends on the engine through exactly one file, and the source package is swappable by
editing only this file (ADR-eval-001).

The store/router imports are lazy (resolved on first use via :func:`load_engine`) so
the plugin imports cleanly when the engine isn't installed; the data-model and
protocol names are needed at import time and are imported eagerly.
"""

from __future__ import annotations

from typing import Any

from memeval.protocols import MemoryStore
from memeval.schema import MemoryItem, RetrievedItem


def load_engine() -> dict[str, Any]:
    """Return the engine's Router + store classes, imported on demand.

    Kept lazy so a missing engine surfaces as a handled construction failure (the
    caller falls back to a fail-open no-op) rather than an import-time crash.
    """
    from memeval.router import Router
    from memeval.stores import GraphStore, MarkdownStore, SqliteVectorStore

    return {
        "Router": Router,
        "GraphStore": GraphStore,
        "MarkdownStore": MarkdownStore,
        "SqliteVectorStore": SqliteVectorStore,
    }


__all__ = ["MemoryItem", "RetrievedItem", "MemoryStore", "load_engine"]
