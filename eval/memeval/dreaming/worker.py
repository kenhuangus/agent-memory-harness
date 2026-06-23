"""Dreaming worker — Job 1 (dedup) detection-only v1.

ADR-dreaming-002 names four jobs the worker is supposed to do (dedup,
contradiction resolution, governance, pruning). v1 ships the *detection*
half of Job 1 only: walk ``store.all()``, group items by a stdlib-only
normalized-content key, return a JSON-serializable governance summary
dict. No item mutation, no merge, no retirement, no embedder.

Detection-only is gated by the public ``MemoryStore`` protocol shape:
there is no ``delete`` method, so cross-session near-duplicates with
different ``item_id`` values cannot be retired inside the protocol. The
mutation half is a follow-up PR after the delete/tombstone contract is
settled.

Rubric: ``eval/memeval/dreaming/tests/INITIAL_DREAM_RUBRIC.md``.
"""

from __future__ import annotations

import re
import string
from typing import Any

from ..protocols import MemoryStore
from ..schema import MemoryItem
from .events import emit

_PUNCT_TRANSLATION = str.maketrans("", "", string.punctuation)
_WHITESPACE_RUN = re.compile(r"\s+")


def _normalize(content: Any) -> str:
    """Lowercase + strip ASCII punctuation + collapse whitespace.

    ``None`` content is coerced to the empty string (§E7 — ``store.all()`` is
    the worker's trust boundary; ``MemoryItem.content`` is typed ``str`` but
    not runtime-enforced).
    """
    if content is None:
        text = ""
    else:
        text = str(content)
    text = text.lower().translate(_PUNCT_TRANSLATION)
    return _WHITESPACE_RUN.sub(" ", text).strip()


class DreamingWorker:
    """Offline memory-consolidation engine — v1: dedup detection only."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def run(self, *, trajectories_path: str | None = None, **kwargs: Any) -> dict:
        """One detection-only pass over ``store.all()``; returns the summary dict."""
        if trajectories_path:
            raise ValueError(
                "trajectories_path not consumed in v1; pass None "
                "(detection-only worker reads store.all() exclusively)"
            )

        items: list[MemoryItem] = list(self.store.all())
        total_items = len(items)

        groups: dict[str, list[str]] = {}
        for item in items:
            key = _normalize(item.content)
            groups.setdefault(key, []).append(item.item_id)

        clusters = [
            {"normalized_key": key, "item_ids": list(ids), "count": len(ids)}
            for key, ids in groups.items()
            if len(ids) >= 2
        ]
        duplicate_clusters = len(clusters)
        items_in_duplicates = sum(c["count"] for c in clusters)

        summary = {
            "schema": "dream.summary",
            "version": 1,
            "mode": "detection",
            "jobs_run": ["dedup_detection"],
            "skipped_jobs": [
                "dedup_merge",
                "contradiction_resolution",
                "governance",
                "pruning",
            ],
            "counts": {
                "total_items": total_items,
                "duplicate_clusters": duplicate_clusters,
                "items_in_duplicates": items_in_duplicates,
            },
            "clusters": clusters,
        }

        emit(
            "dream.summary",
            mode=summary["mode"],
            total_items=total_items,
            duplicate_clusters=duplicate_clusters,
        )

        return summary


def dream(store: MemoryStore, **kwargs: Any) -> dict:
    """Convenience: run one :class:`DreamingWorker` pass over ``store``."""
    return DreamingWorker(store).run(**kwargs)


__all__ = ["DreamingWorker", "dream"]
