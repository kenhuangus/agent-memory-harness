"""The single import edge between the plugin and the eval package's frozen contract.

The memory system is destined to live in its own package, with the eval engine
treating it as a black box (ADR-eval-001). Until that extraction happens, the
plugin reaches the frozen data model (``MemoryItem``, ``RetrievedItem``) and the
``MemoryStore`` protocol from ``memeval`` — but it does so *only here*. Every other
module in the plugin imports these names from this file, never from ``memeval``
directly. When the contract becomes a standalone package, only this file changes;
the rest of the plugin moves unedited.

This file is the *only* place the plugin depends on ``eval/memeval``.
"""

from __future__ import annotations

from memeval.protocols import MemoryStore
from memeval.schema import MemoryItem, RetrievedItem

__all__ = ["MemoryItem", "RetrievedItem", "MemoryStore"]
