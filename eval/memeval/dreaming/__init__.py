"""Async "dreaming" consolidation — owner: **Scott B.** (@NerdAlert58).

Runs offline while agents sleep and does four jobs over a
:class:`memeval.protocols.MemoryStore` (+ the ``Trajectory`` JSONL logs from
``memeval.trajectory``):

1. **Deduplicate** across backends (exact / semantic / near-duplicate).
2. **Resolve contradictions** (by recency / confidence / source).
3. **Session governance** — must-know / must-do / blacklist.
4. **Selective retention & pruning** so the store doesn't grow unbounded.

Scaffold — the worker raises :class:`NotImplementedError` until implemented.
Importing this package is stdlib-only; heavy deps load lazily where needed.
"""

from __future__ import annotations

from typing import Any

__all__ = ["dream", "DreamingWorker"]


def __getattr__(name: str) -> Any:  # lazy re-export
    if name in ("dream", "DreamingWorker"):
        from . import worker
        return getattr(worker, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
