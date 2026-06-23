"""Dreaming worker — Jobs 1 (dedup) + 4 (TTL pruning) detection + mutation.

Layered job-by-job per ADR-002:
- **Job 1 (dedup) detection-only** shipped in PR #88: walk ``store.all()``,
  group items by stdlib-normalized content, return a JSON summary dict.
- **Job 1 mutation** shipped in PR #98 per ADR-021: under a basedir
  ``flock``, retire each cluster's losers via ``self.store.delete()`` (frozen
  protocol per PR #99).
- **Job 4 (TTL pruning) detection + mutation** shipped in PR after #98 per
  ``JOB4_TTL_RUBRIC.md``: before clustering, drop items whose
  ``(now - item.timestamp) > retention_seconds`` using the SAME basedir lock
  and the SAME ``self.store.delete()`` primitive.

Mutation contract is hard-delete, no CAS, no winner-write-back. The
Daydream-vs-Dream race (Shape 2) is closed by ``engine.daydream`` acquiring
the same basedir lock before the per-session lock (ADR-021 Decision 4).

Rubrics:
- ``eval/memeval/dreaming/tests/JOB1_MUTATION_RUBRIC.md`` (dedup half)
- ``eval/memeval/dreaming/tests/JOB4_TTL_RUBRIC.md`` (TTL half)
"""

from __future__ import annotations

import logging
import os
import re
import string
import time
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
_SECONDS_PER_DAY: int = 86400
_DEFAULT_ITEM_RETENTION_DAYS: int = 30


def _now() -> float:
    """Module-level seam for ``time.time()`` — monkeypatchable in tests (JOB4 §J-TTL-1)."""
    return time.time()


def _read_item_retention_days() -> int:
    """Resolve ``$DREAM_ITEM_RETENTION_DAYS`` to an int days value.

    Per JOB4 Open-contracts pin #4/#9/#10:
    - Unset → default 30 days (pin #5).
    - ``"0"`` → 0 (treated as DISABLED by caller; not a magic prune-everything).
    - Negative or non-integer → 30-day default with a warning log (mirrors
      ADR-015's ``_read_ttl_days`` bounds-check behavior).
    """
    raw = os.environ.get("DREAM_ITEM_RETENTION_DAYS")
    if raw is None or raw == "":
        return _DEFAULT_ITEM_RETENTION_DAYS
    try:
        value = int(raw)
    except ValueError:
        log.warning(
            "DREAM_ITEM_RETENTION_DAYS=%r is not an integer; falling back to %d-day default",
            raw, _DEFAULT_ITEM_RETENTION_DAYS,
        )
        return _DEFAULT_ITEM_RETENTION_DAYS
    if value < 0:
        log.warning(
            "DREAM_ITEM_RETENTION_DAYS=%d is negative; falling back to %d-day default",
            value, _DEFAULT_ITEM_RETENTION_DAYS,
        )
        return _DEFAULT_ITEM_RETENTION_DAYS
    return value


def _pick_pruned(items: list[MemoryItem], now: float, retention_seconds: int) -> list[str]:
    """Return the lex-sorted item_ids whose age strictly exceeds retention.

    JOB4 §F-TTL-3 (strictly greater) + §B13 (sorted ascending in the dict).
    Sorting at selection time keeps the dict-field invariant locally enforced.
    """
    pruned = [item.item_id for item in items if (now - item.timestamp) > retention_seconds]
    return sorted(pruned)


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
        """One detection+mutation+pruning pass; returns the summary dict.

        Order of operations (JOB4 Open-contracts pin #7 — TTL BEFORE dedup;
        JOB1 §F12 — deletes complete BEFORE summary emit):
        1. Reject truthy ``trajectories_path`` BEFORE any lock or store access.
        2. NFS detection BEFORE basedir lock.
        3. Acquire basedir flock.
        4. Walk ``store.all()``.
        5. TTL pass: select pruned ids; call ``self.store.delete()`` on each.
        6. Re-scan surviving items + cluster by normalized content.
        7. Dedup pass: pick winner per cluster; delete losers.
        8. Build the summary dict from the completed deletes.
        9. Emit ``dream.summary``.
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

            # JOB4 §D-TTL-4: exactly one call to _now() per run().
            retention_days = _read_item_retention_days()
            retention_seconds = retention_days * _SECONDS_PER_DAY

            # JOB4 pin #9: retention_days == 0 disables pruning.
            if retention_days == 0:
                pruned_ids: list[str] = []
            else:
                now = _now()
                pruned_ids = _pick_pruned(items, now, retention_seconds)

            # JOB4 §F-TTL-2: TTL deletes complete BEFORE dedup deletes.
            for pid in pruned_ids:
                self.store.delete(pid)

            pruned_set = set(pruned_ids)
            survivors = [it for it in items if it.item_id not in pruned_set]

            groups: dict[str, list[MemoryItem]] = {}
            for item in survivors:
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

            # JOB1 §F12 + JOB4 §F-TTL-13: dedup deletes after TTL deletes,
            # both complete BEFORE summary dict is constructed.
            for cluster in cluster_specs:
                for retired_id in cluster["retired_ids"]:
                    self.store.delete(retired_id)

            duplicate_clusters = len(cluster_specs)
            items_in_duplicates = sum(c["count"] for c in cluster_specs)
            items_retired = sum(len(c["retired_ids"]) for c in cluster_specs)
            items_pruned = len(pruned_ids)

            summary = {
                "schema": "dream.summary",
                "version": 1,
                "mode": "detection_and_mutation_and_pruning",
                "jobs_run": ["dedup_detection", "dedup_merge", "ttl_pruning"],
                "skipped_jobs": [
                    "contradiction_resolution",
                    "governance",
                ],
                "counts": {
                    "total_items": total_items,
                    "duplicate_clusters": duplicate_clusters,
                    "items_in_duplicates": items_in_duplicates,
                    "items_retired": items_retired,
                    "items_pruned": items_pruned,
                    "retention_seconds_effective": retention_seconds,
                },
                "clusters": cluster_specs,
                "pruned": {
                    "item_ids": list(pruned_ids),
                    "retention_seconds_effective": retention_seconds,
                },
            }

            emit(
                "dream.summary",
                mode=summary["mode"],
                total_items=total_items,
                duplicate_clusters=duplicate_clusters,
                items_retired=items_retired,
                items_pruned=items_pruned,
                retention_seconds_effective=retention_seconds,
            )

            return summary


def dream(store: MemoryStore, **kwargs: Any) -> dict:
    """Convenience: run one :class:`DreamingWorker` pass over ``store``."""
    return DreamingWorker(store).run(**kwargs)


__all__ = ["DreamingWorker", "dream"]
