"""Harness-agnostic plugin core: the MemoryClient, recall/remember, events.

Everything in ``core`` is independent of any specific coding harness. The Claude
Code adapter (and future adapters) construct a :class:`~cookbook_memory.core.client.MemoryClient`
and call ``recall``/``remember`` on it; the client builds the memory engine (Brent's
Router + the store backends) once and runs every operation through it, with events and
fail-open behavior. The plugin holds no routing or storage logic of its own.
"""

from __future__ import annotations

from .client import Hit, MemoryClient, build_engine
from .config import Settings
from .events import EventStream, event

__all__ = [
    "MemoryClient",
    "Hit",
    "build_engine",
    "EventStream",
    "event",
    "Settings",
]
