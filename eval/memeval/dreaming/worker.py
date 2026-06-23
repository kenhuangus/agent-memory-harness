"""Dreaming worker — Job 1 (dedup) detection + mutation per ADR-021.

v1 (PR #88) shipped detection-only: walk ``store.all()``, group items by a
stdlib-normalized content key, return a JSON-serializable governance summary
dict. v2 (this) extends to **mutation**: under a basedir-scope `flock`
(ADR-021 Decision 2), the worker retires each cluster's losers via
``Router.delete()`` (ADR-021 Decision 1) and reports `winner_id` + `retired_ids`
per cluster.

The mutation contract is hard-delete, no CAS, no winner-write-back — the
surviving item is the original cluster winner with content/relevancy/version
unchanged. Daydream-vs-Dream race (Shape 2) is closed by ``engine.daydream``
acquiring the same basedir lock before the per-session lock (ADR-021
Decision 4).

Rubric: ``eval/memeval/dreaming/tests/JOB1_MUTATION_RUBRIC.md``.
"""

from __future__ import annotations

import logging
import os
import re
import string
from pathlib import Path
from typing import Any

from ..protocols import MemoryStore
from ..schema import MemoryItem
from ._state import (
    _DreamLockHeld,
    _UnsupportedFsError,
    _basedir_dream_lock,
    _is_network_fs,
)
from .events import emit

log = logging.getLogger(__name__)

_PUNCT_TRANSLATION = str.maketrans("", "", string.punctuation)
_WHITESPACE_RUN = re.compile(r"\s+")


def _normalize(content: Any) -> str:
    """Lowercase + strip ASCII punctuation + collapse whitespace; ``None`` → ``""``."""
    if content is None:
        text = ""
    else:
        text = str(content)
    text = text.lower().translate(_PUNCT_TRANSLATION)
    return _WHITESPACE_RUN.sub(" ", text).strip()


def _resolve_basedir() -> Path:
    """Per rubric preamble pin #4: ``Path($MEMORY_STORE)`` when set, else CWD fallback."""
    raw = os.environ.get("MEMORY_STORE")
    if raw:
        return Path(raw)
    return Path.cwd()


def _pick_winner(items: list[MemoryItem]) -> str:
    """Latest ``item.timestamp`` wins; ties broken by lexicographically lowest ``item_id``.

    Pinned by rubric preamble Open-contracts pin #3 + §D5a/D5b.
    """
    return sorted(items, key=lambda i: (-i.timestamp, i.item_id))[0].item_id


class DreamingWorker:
    """Offline memory-consolidation engine — Job 1 (dedup) detection + mutation."""

    def __init__(self, store: MemoryStore) -> None:
        """Bind the worker to the ``MemoryStore`` it will read + mutate during ``run()``."""
        self.store = store

    def run(self, *, trajectories_path: str | None = None, **kwargs: Any) -> dict:
        """One detection+mutation pass; returns the summary dict.

        Order of operations (rubric §F12 — deletes complete BEFORE summary built):
        1. Reject truthy ``trajectories_path`` BEFORE any lock or store access (rubric §G4).
        2. NFS detection BEFORE basedir lock (rubric §L17).
        3. Acquire basedir flock (rubric §L13/L14).
        4. Walk ``store.all()`` and cluster by normalized content.
        5. For every cluster, pick winner + call ``self.store.delete()`` on every loser.
        6. Build the summary dict from the completed deletes.
        7. Emit ``dream.summary`` event.
        """
        if trajectories_path:
            raise ValueError(
                "trajectories_path not consumed in v1; pass None "
                "(detection+mutation worker reads store.all() exclusively)"
            )

        basedir = _resolve_basedir()
        if _is_network_fs(basedir):
            if os.environ.get("DREAM_ALLOW_NETWORK_FS") == "1":
                log.warning(
                    "DREAM_ALLOW_NETWORK_FS=1 set; proceeding against detected network FS at %s",
                    basedir,
                )
            else:
                raise _UnsupportedFsError(
                    f"basedir {basedir} appears to be on a network filesystem; "
                    f"set DREAM_ALLOW_NETWORK_FS=1 to override"
                )

        with _basedir_dream_lock(basedir):
            items: list[MemoryItem] = list(self.store.all())
            total_items = len(items)

            groups: dict[str, list[MemoryItem]] = {}
            for item in items:
                key = _normalize(item.content)
                groups.setdefault(key, []).append(item)

            cluster_specs: list[dict] = []
            for key, group_items in groups.items():
                if len(group_items) < 2:
                    continue
                item_ids = [i.item_id for i in group_items]
                winner_id = _pick_winner(group_items)
                retired_ids = [iid for iid in item_ids if iid != winner_id]
                cluster_specs.append(
                    {
                        "normalized_key": key,
                        "item_ids": list(item_ids),
                        "count": len(item_ids),
                        "winner_id": winner_id,
                        "retired_ids": list(retired_ids),
                    }
                )

            # §F12: all deletes complete BEFORE summary dict is constructed.
            # `delete` is part of the frozen `MemoryStore` protocol per PR #99
            # (Brent's [CONTRACT] PR landed before this PR merged).
            for cluster in cluster_specs:
                for retired_id in cluster["retired_ids"]:
                    self.store.delete(retired_id)

            duplicate_clusters = len(cluster_specs)
            items_in_duplicates = sum(c["count"] for c in cluster_specs)
            items_retired = sum(len(c["retired_ids"]) for c in cluster_specs)

            summary = {
                "schema": "dream.summary",
                "version": 1,
                "mode": "detection_and_mutation",
                "jobs_run": ["dedup_detection", "dedup_merge"],
                "skipped_jobs": [
                    "contradiction_resolution",
                    "governance",
                    "pruning",
                ],
                "counts": {
                    "total_items": total_items,
                    "duplicate_clusters": duplicate_clusters,
                    "items_in_duplicates": items_in_duplicates,
                    "items_retired": items_retired,
                },
                "clusters": cluster_specs,
            }

            emit(
                "dream.summary",
                mode=summary["mode"],
                total_items=total_items,
                duplicate_clusters=duplicate_clusters,
                items_retired=items_retired,
            )

            return summary


def dream(store: MemoryStore, **kwargs: Any) -> dict:
    """Convenience: run one :class:`DreamingWorker` pass over ``store``."""
    return DreamingWorker(store).run(**kwargs)


__all__ = ["DreamingWorker", "dream"]
