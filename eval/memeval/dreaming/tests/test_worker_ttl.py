"""Worker tests — every unit-test criterion from JOB4_TTL_RUBRIC.md §A-M.

Job 4 adds TTL-based pruning on top of Job 1's dedup detection+mutation:
before clustering, items past `DREAM_ITEM_RETENTION_DAYS` are dropped via
the same `self.store.delete()` primitive frozen into the MemoryStore
protocol per PR #99.

Job 1's lock + NFS + Daydream surface tests live in `test_worker_mutation.py`
and are preserved (this rubric's §L1 / §M1 / §I4 rely on those).

Shell-command criteria (§A4, §F-TTL-10/11/12/14, §H-TTL-7, §I5, §J-TTL-2/3/5/6,
§K3/K4/K8/K11/K14/K15) are run verbatim from the rubric and not duplicated here.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from memeval.dreaming import _state, worker
from memeval.dreaming._state import _DreamLockHeld, _UnsupportedFsError
from memeval.harness import InMemoryStore
from memeval.schema import MemoryItem


# --------------------------------------------------------------------------- #
# Shared helpers + fixtures
# --------------------------------------------------------------------------- #


class _DeleteAwareStore(InMemoryStore):
    """InMemoryStore subclass adding `delete()` so the worker's call dispatches correctly."""

    def delete(self, item_id: str) -> bool:
        """Hard-delete `item_id` from the in-memory dict; idempotent."""
        if item_id in self._items:
            del self._items[item_id]
            self._order = [i for i in self._order if i != item_id]
            return True
        return False


_FIXED_NOW: float = 1_700_000_000.0  # arbitrary fixed Unix epoch for deterministic tests
_DAY: int = 86400
_THIRTY_DAYS: int = 30 * _DAY


@pytest.fixture(autouse=True)
def _no_network_fs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to non-network FS so NFS hard-fail doesn't fire."""
    monkeypatch.setattr("memeval.dreaming.worker._is_network_fs", lambda path: False)


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to a fixed _now() so age math is deterministic."""
    monkeypatch.setattr("memeval.dreaming.worker._now", lambda: _FIXED_NOW)


@pytest.fixture(autouse=True)
def _default_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to the 30-day retention unless overridden."""
    monkeypatch.delenv("DREAM_ITEM_RETENTION_DAYS", raising=False)


@pytest.fixture
def memory_store_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temp MEMORY_STORE directory and set the env-var."""
    store = tmp_path / "memory-store"
    store.mkdir()
    monkeypatch.setenv("MEMORY_STORE", str(store))
    return store


@pytest.fixture
def spy_emit(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Capture every emit call through worker.emit + _state.emit."""
    captured: list[tuple[str, dict[str, Any]]] = []

    def _fake(event_type: str, **fields: Any) -> None:
        """Spy replacement — record (event_type, fields)."""
        captured.append((event_type, fields))

    monkeypatch.setattr("memeval.dreaming.worker.emit", _fake)
    monkeypatch.setattr("memeval.dreaming._state.emit", _fake)
    monkeypatch.setattr("memeval.dreaming.engine.emit", _fake)
    return captured


def _seed(now: float, *specs: tuple[str, str, float]) -> _DeleteAwareStore:
    """Build a store from (item_id, content, age_in_days_negative_for_future) triples.

    `age_days`: how many days OLD the item is at `now`. Positive = past. Use
    explicit `timestamp = now - age * _DAY` to be deterministic.
    """
    store = _DeleteAwareStore()
    for item_id, content, age_days in specs:
        store.write(MemoryItem(item_id=item_id, content=content, timestamp=now - age_days * _DAY))
    return store


# --------------------------------------------------------------------------- #
# §A — Surface (Job-4-specific cases; A4 is shell)
# --------------------------------------------------------------------------- #


def test_run_returns_dict_after_ttl_prune(memory_store_dir: Path) -> None:
    """A1 — one item past TTL + one fresh: returns dict, no raise."""
    store = _seed(_FIXED_NOW, ("stale", "x", 60), ("fresh", "y", 1))
    result = worker.DreamingWorker(store).run()
    assert isinstance(result, dict)


def test_run_empty_store_no_ttl_deletes(memory_store_dir: Path) -> None:
    """A2 — empty store: returns dict, zero delete calls."""
    store = _DeleteAwareStore()
    spy = MagicMock(wraps=store.delete)
    store.delete = spy  # type: ignore[method-assign]
    result = worker.DreamingWorker(store).run()
    assert isinstance(result, dict)
    assert spy.call_count == 0


def test_run_no_ttl_victims_zero_pruned(memory_store_dir: Path) -> None:
    """A3 — store with no items past TTL + no dup clusters: items_pruned=0, pruned.item_ids=[]."""
    store = _seed(_FIXED_NOW, ("a", "alpha", 1), ("b", "beta", 2))
    result = worker.DreamingWorker(store).run()
    assert result["counts"]["items_pruned"] == 0
    assert result["pruned"]["item_ids"] == []


# --------------------------------------------------------------------------- #
# §B — Dict shape
# --------------------------------------------------------------------------- #

# Pinned top-level + counts shape — UPDATED 2026-06-23 by Job 3 PR per
# JOB3_GOVERNANCE_RUBRIC.md "Supersedes" — Job 3 extends the dict with a
# top-level `governance` block and 8 new `counts` entries. Job 2 had
# already extended with `contradicted` + 6 cost-observability entries.
# These tests preserve the TTL-specific properties under the
# Job-3-extended shape.
_EXPECTED_TOP_LEVEL_KEYS = {
    "schema", "version", "mode", "jobs_run",
    "skipped_jobs", "counts", "clusters", "pruned", "contradicted",
    "governance",
}

_EXPECTED_COUNTS_KEYS = {
    "total_items", "duplicate_clusters", "items_in_duplicates",
    "items_retired", "items_pruned", "retention_seconds_effective",
    "items_contradicted", "contradiction_llm_calls",
    "contradiction_input_tokens", "contradiction_output_tokens",
    "contradiction_cost_usd_estimate", "contradiction_pairs_examined_estimate",
    "items_blacklisted", "items_must_known", "items_must_done",
    "governance_llm_calls", "governance_input_tokens",
    "governance_output_tokens", "governance_cost_usd_estimate",
    "governance_items_examined_estimate",
}


def test_ttl_top_level_keys_exact(memory_store_dir: Path) -> None:
    """B1 — top-level key set is exactly the Job-3-extended pinned set."""
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 1))).run()
    assert set(result.keys()) == _EXPECTED_TOP_LEVEL_KEYS


def test_ttl_schema_literal(memory_store_dir: Path) -> None:
    """B2 — schema == 'dream.summary'."""
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 1))).run()
    assert result["schema"] == "dream.summary"


def test_ttl_version_literal(memory_store_dir: Path) -> None:
    """B3 — version == 1, type int."""
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 1))).run()
    assert result["version"] == 1
    assert type(result["version"]) is int


def test_ttl_mode_literal(memory_store_dir: Path) -> None:
    """B4 — Job 3 supersedes Job 2 §B4: mode adds `_and_governance` suffix."""
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 1))).run()
    assert result["mode"] == "detection_and_mutation_and_pruning_and_contradiction_and_governance"


def test_ttl_jobs_run_literal(memory_store_dir: Path) -> None:
    """B5 — Job 3 supersedes Job 2 §B5: jobs_run adds `governance`."""
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 1))).run()
    assert result["jobs_run"] == [
        "dedup_detection", "dedup_merge", "ttl_pruning",
        "contradiction_resolution", "governance",
    ]


def test_ttl_skipped_jobs_literal(memory_store_dir: Path) -> None:
    """B6 — Job 3 supersedes Job 2 §B6: governance removed → skipped_jobs is now empty."""
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 1))).run()
    assert result["skipped_jobs"] == []


def test_ttl_counts_key_set_exact(memory_store_dir: Path) -> None:
    """B7 — Job 3 extends counts with 8 new keys (3 size + 4 cost + 1 examined-estimate)."""
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 1))).run()
    assert set(result["counts"].keys()) == _EXPECTED_COUNTS_KEYS


def test_ttl_counts_values_are_int(memory_store_dir: Path) -> None:
    """B8 — Job 3 supersedes Job 2 §B8: TWO float keys (contradiction_cost_usd_estimate
    + governance_cost_usd_estimate); all 18 other keys are strict int."""
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 1))).run()
    float_keys = {"contradiction_cost_usd_estimate", "governance_cost_usd_estimate"}
    for k, v in result["counts"].items():
        if k in float_keys:
            assert type(v) is float, f"{k} = {v!r} ({type(v).__name__}); expected float"
        else:
            assert type(v) is int, f"{k} = {v!r} ({type(v).__name__}); expected int"


def test_ttl_pruned_key_set_exact(memory_store_dir: Path) -> None:
    """B9 — pruned has key set {item_ids, retention_seconds_effective}."""
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 1))).run()
    assert set(result["pruned"].keys()) == {"item_ids", "retention_seconds_effective"}


def test_ttl_pruned_item_ids_is_list_of_str(memory_store_dir: Path) -> None:
    """B10 — pruned.item_ids is list[str]."""
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 60), ("b", "y", 1))).run()
    assert isinstance(result["pruned"]["item_ids"], list)
    for iid in result["pruned"]["item_ids"]:
        assert isinstance(iid, str)


def test_ttl_retention_seconds_consistent_across_summary(memory_store_dir: Path) -> None:
    """B11 — pruned.retention_seconds_effective equals counts.retention_seconds_effective."""
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 1))).run()
    assert result["pruned"]["retention_seconds_effective"] == result["counts"]["retention_seconds_effective"]


def test_ttl_result_json_roundtrip(memory_store_dir: Path) -> None:
    """B12 — JSON roundtrip preserves equality."""
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 60), ("b", "y", 1))).run()
    assert json.loads(json.dumps(result)) == result


def test_ttl_pruned_item_ids_sorted_ascending(memory_store_dir: Path) -> None:
    """B13 — pruned.item_ids is lexicographically sorted."""
    store = _seed(_FIXED_NOW, ("zeta", "x", 60), ("alpha", "y", 60), ("mu", "z", 60))
    result = worker.DreamingWorker(store).run()
    assert result["pruned"]["item_ids"] == sorted(result["pruned"]["item_ids"])


# --------------------------------------------------------------------------- #
# §C — Counts arithmetic
# --------------------------------------------------------------------------- #


def test_ttl_items_pruned_equals_len_pruned_ids(memory_store_dir: Path) -> None:
    """C-TTL-1 — counts.items_pruned == len(pruned.item_ids)."""
    result = worker.DreamingWorker(
        _seed(_FIXED_NOW, ("a", "x", 60), ("b", "y", 60), ("c", "z", 1))
    ).run()
    assert result["counts"]["items_pruned"] == len(result["pruned"]["item_ids"])


def test_ttl_total_items_pre_run(memory_store_dir: Path) -> None:
    """C-TTL-2 — total_items equals len(store.all()) BEFORE run."""
    store = _seed(_FIXED_NOW, ("a", "x", 60), ("b", "y", 1))
    pre = len(store.all())
    result = worker.DreamingWorker(store).run()
    assert result["counts"]["total_items"] == pre


def test_ttl_store_size_after_run_accounts_for_both_paths(memory_store_dir: Path) -> None:
    """C-TTL-3 — len(store.all()) == total - retired - pruned."""
    store = _seed(_FIXED_NOW, ("stale", "x", 60), ("a", "y", 1), ("b", "y", 2))
    result = worker.DreamingWorker(store).run()
    assert len(store.all()) == result["counts"]["total_items"] - result["counts"]["items_retired"] - result["counts"]["items_pruned"]


def test_ttl_pruned_disjoint_from_retired(memory_store_dir: Path) -> None:
    """C-TTL-4 — pruned.item_ids disjoint from union of cluster retired_ids."""
    store = _seed(_FIXED_NOW, ("stale", "x", 60), ("a", "y", 1), ("b", "y", 2))
    result = worker.DreamingWorker(store).run()
    pruned_set = set(result["pruned"]["item_ids"])
    retired_set = {iid for c in result["clusters"] for iid in c["retired_ids"]}
    assert pruned_set.isdisjoint(retired_set)


def test_ttl_pruned_disjoint_from_winners(memory_store_dir: Path) -> None:
    """C-TTL-5 — pruned.item_ids disjoint from cluster winner_ids."""
    store = _seed(_FIXED_NOW, ("stale", "x", 60), ("a", "y", 1), ("b", "y", 2))
    result = worker.DreamingWorker(store).run()
    pruned_set = set(result["pruned"]["item_ids"])
    winners = {c["winner_id"] for c in result["clusters"]}
    assert pruned_set.isdisjoint(winners)


def test_ttl_retention_seconds_effective_matches_env(memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """C-TTL-6 — counts.retention_seconds_effective reports the Memory-type
    retention (the pre-V5 / off-list fallback in `TYPE_RETENTION_DAYS`).

    ADR-028 §1 amended this: `DREAM_ITEM_RETENTION_DAYS` is kill-switch-only
    in v2 — non-zero values are IGNORED. The summary field reports the
    Memory-type retention (matching today's flat 30-day default for untyped
    content) regardless of what the env var is set to.
    """
    monkeypatch.setenv("DREAM_ITEM_RETENTION_DAYS", "7")
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 1))).run()
    assert result["counts"]["retention_seconds_effective"] == 30 * _DAY


# --------------------------------------------------------------------------- #
# §D — Determinism
# --------------------------------------------------------------------------- #


def test_ttl_deterministic_under_fixed_now(memory_store_dir: Path) -> None:
    """D-TTL-1 — same fixture twice → same pruned.item_ids."""
    store_a = _seed(_FIXED_NOW, ("a", "x", 60), ("b", "y", 60))
    store_b = _seed(_FIXED_NOW, ("a", "x", 60), ("b", "y", 60))
    ra = worker.DreamingWorker(store_a).run()
    rb = worker.DreamingWorker(store_b).run()
    assert ra["pruned"]["item_ids"] == rb["pruned"]["item_ids"]


def test_ttl_second_run_is_noop(memory_store_dir: Path) -> None:
    """D-TTL-2 — second run after first has items_pruned == 0."""
    store = _seed(_FIXED_NOW, ("a", "x", 60), ("b", "y", 1))
    worker.DreamingWorker(store).run()
    second = worker.DreamingWorker(store).run()
    assert second["counts"]["items_pruned"] == 0
    assert second["pruned"]["item_ids"] == []


def test_ttl_pruning_precedes_clustering(memory_store_dir: Path) -> None:
    """D-TTL-3 — TTL deletes happen before clustering sees the items."""
    # Seed: two items with the SAME normalized content, ONE past TTL.
    # If TTL ran AFTER clustering, both would be in a cluster, retire one.
    # If TTL runs FIRST, only the survivor remains; cluster size 1 → no cluster.
    store = _seed(_FIXED_NOW, ("stale-dup", "shared", 60), ("fresh-dup", "shared", 1))
    result = worker.DreamingWorker(store).run()
    assert result["pruned"]["item_ids"] == ["stale-dup"]
    assert result["clusters"] == []
    assert {i.item_id for i in store.all()} == {"fresh-dup"}


def test_ttl_now_called_exactly_once(memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-TTL-4 — worker._now() called exactly once per run()."""
    calls = []

    def _spy_now():
        """Spy _now to count invocations."""
        calls.append(_FIXED_NOW)
        return _FIXED_NOW

    monkeypatch.setattr("memeval.dreaming.worker._now", _spy_now)
    worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 1))).run()
    assert len(calls) == 1


def test_ttl_preempts_cluster_winner_when_winner_is_stale(memory_store_dir: Path) -> None:
    """D-TTL-5 — TTL-first ordering preempts cluster formation; dedup never charges items_retired."""
    # Both items cluster on the same normalized key. One is past TTL, one is
    # fresh. Per pin #7, TTL runs first → stale item pruned → only one item
    # left for clustering → no cluster forms → items_retired = 0 (dedup-loser
    # path was never charged).
    store = _seed(_FIXED_NOW, ("stale-sibling", "shared text", 60), ("fresh-survivor", "shared text", 1))
    result = worker.DreamingWorker(store).run()
    assert "stale-sibling" in result["pruned"]["item_ids"]
    assert result["clusters"] == []
    assert {i.item_id for i in store.all()} == {"fresh-survivor"}
    # D-TTL-5 (c): items_retired == 0 — dedup-loser count is 0 because the
    # cluster never formed (TTL preempted). This is the load-bearing assertion
    # that distinguishes TTL-first from dedup-first orderings.
    assert result["counts"]["items_retired"] == 0


# --------------------------------------------------------------------------- #
# §E — Normalization preserved (delegating to dedup branch)
# --------------------------------------------------------------------------- #


def test_ttl_dedup_normalization_unchanged_when_no_prune(memory_store_dir: Path) -> None:
    """E1 — with all timestamps fresh, dedup normalization behaves as Job 1."""
    store = _seed(_FIXED_NOW, ("a", "Hello, world!", 1), ("b", "hello world", 1))
    result = worker.DreamingWorker(store).run()
    assert len(result["clusters"]) == 1
    assert result["counts"]["items_pruned"] == 0


# --------------------------------------------------------------------------- #
# §F — Mutation contract — TTL invariants
# --------------------------------------------------------------------------- #


def test_ttl_total_delete_call_count_equals_both_paths(memory_store_dir: Path) -> None:
    """F-TTL-1 — total delete calls == items_retired + items_pruned."""
    store = _seed(_FIXED_NOW, ("stale", "x", 60), ("a", "y", 1), ("b", "y", 2))
    spy = MagicMock(wraps=store.delete)
    store.delete = spy  # type: ignore[method-assign]
    result = worker.DreamingWorker(store).run()
    assert spy.call_count == result["counts"]["items_retired"] + result["counts"]["items_pruned"]


def test_ttl_deletes_complete_before_dedup_deletes(memory_store_dir: Path) -> None:
    """F-TTL-2 — TTL deletes complete before dedup-loser deletes (monotonic_ns ordering)."""
    store = _seed(_FIXED_NOW, ("stale-1", "x", 60), ("stale-2", "y", 60), ("a", "z", 1), ("b", "z", 2))
    completions: list[tuple[str, int]] = []
    real_delete = store.delete

    def _spy_delete(item_id):
        """Record (item_id, monotonic_ns at completion)."""
        result = real_delete(item_id)
        completions.append((item_id, time.monotonic_ns()))
        return result

    store.delete = _spy_delete  # type: ignore[method-assign]
    result = worker.DreamingWorker(store).run()
    pruned_set = set(result["pruned"]["item_ids"])
    retired_set = {iid for c in result["clusters"] for iid in c["retired_ids"]}
    # Every TTL completion precedes every dedup completion
    ttl_max = max(ts for (iid, ts) in completions if iid in pruned_set)
    dedup_min = min(ts for (iid, ts) in completions if iid in retired_set)
    assert ttl_max <= dedup_min


def test_ttl_boundary_strict_greater_than(memory_store_dir: Path) -> None:
    """F-TTL-3 — boundary: age exactly retention is NOT pruned."""
    # age == 30 days exactly: timestamp == now - 30 * 86400 → not pruned
    store = _seed(_FIXED_NOW, ("at-edge", "x", 30))
    result = worker.DreamingWorker(store).run()
    assert "at-edge" not in result["pruned"]["item_ids"]
    assert "at-edge" in {i.item_id for i in store.all()}


def test_ttl_one_second_past_boundary_pruned(memory_store_dir: Path) -> None:
    """F-TTL-4 — one second past boundary IS pruned."""
    # age = 30 days + 1 second: timestamp = now - (30 * 86400 + 1)
    store = _DeleteAwareStore()
    store.write(MemoryItem(item_id="just-past", content="x", timestamp=_FIXED_NOW - _THIRTY_DAYS - 1))
    result = worker.DreamingWorker(store).run()
    assert "just-past" in result["pruned"]["item_ids"]
    assert store.get("just-past") is None


def test_ttl_every_ttl_delete_targets_a_pruned_id(memory_store_dir: Path) -> None:
    """F-TTL-5 — every delete arg on TTL path is in pruned.item_ids."""
    store = _seed(_FIXED_NOW, ("stale", "x", 60), ("a", "y", 1), ("b", "y", 2))
    completions: list[str] = []
    real_delete = store.delete

    def _spy_delete(item_id):
        """Record delete completions in call-order."""
        result = real_delete(item_id)
        completions.append(item_id)
        return result

    store.delete = _spy_delete  # type: ignore[method-assign]
    result = worker.DreamingWorker(store).run()
    pruned = set(result["pruned"]["item_ids"])
    # The first len(pruned) completions should be the TTL path
    ttl_calls = set(completions[: len(pruned)])
    assert ttl_calls == pruned


def test_ttl_no_fresh_item_pruned(memory_store_dir: Path) -> None:
    """F-TTL-6 — no item with age <= retention is pruned."""
    store = _seed(_FIXED_NOW, ("a", "x", 1), ("b", "y", 29), ("c", "z", 30))
    result = worker.DreamingWorker(store).run()
    assert result["pruned"]["item_ids"] == []


def test_ttl_zero_timestamp_is_pruned(memory_store_dir: Path) -> None:
    """F-TTL-7 — item.timestamp == 0.0 is pruned (treated as legitimately-old)."""
    store = _DeleteAwareStore()
    store.write(MemoryItem(item_id="zero-ts", content="x", timestamp=0.0))
    result = worker.DreamingWorker(store).run()
    assert "zero-ts" in result["pruned"]["item_ids"]


def test_ttl_pruned_ids_absent_after_run(memory_store_dir: Path) -> None:
    """F-TTL-8 — pruned items return None from store.get."""
    store = _seed(_FIXED_NOW, ("s1", "x", 60), ("s2", "y", 60), ("fresh", "z", 1))
    result = worker.DreamingWorker(store).run()
    for pid in result["pruned"]["item_ids"]:
        assert store.get(pid) is None


# --------------------------------------------------------------------------- #
# §H — DREAM_ITEM_RETENTION_DAYS env handling
# --------------------------------------------------------------------------- #


def test_ttl_default_retention_30_days(memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """H-TTL-1 — DREAM_ITEM_RETENTION_DAYS unset → 30-day default."""
    monkeypatch.delenv("DREAM_ITEM_RETENTION_DAYS", raising=False)
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 1))).run()
    assert result["counts"]["retention_seconds_effective"] == 30 * _DAY


def test_ttl_zero_retention_disables_pruning(memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """H-TTL-2 — DREAM_ITEM_RETENTION_DAYS=0 disables TTL pruning."""
    monkeypatch.setenv("DREAM_ITEM_RETENTION_DAYS", "0")
    # Seed an item with timestamp=0.0 that would normally be pruned.
    store = _DeleteAwareStore()
    store.write(MemoryItem(item_id="zero-ts", content="x", timestamp=0.0))
    result = worker.DreamingWorker(store).run()
    assert result["counts"]["items_pruned"] == 0
    assert result["pruned"]["item_ids"] == []
    assert {i.item_id for i in store.all()} == {"zero-ts"}


def test_ttl_negative_env_falls_back_to_default(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """H-TTL-4 — negative retention falls back to 30-day default with warning."""
    monkeypatch.setenv("DREAM_ITEM_RETENTION_DAYS", "-5")
    caplog.set_level(logging.WARNING, logger="memeval.dreaming.worker")
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 1))).run()
    assert result["counts"]["retention_seconds_effective"] == 30 * _DAY
    assert any("negative" in rec.getMessage().lower() for rec in caplog.records)


def test_ttl_non_integer_env_falls_back_to_default(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """H-TTL-3 — non-integer retention falls back to 30-day default with warning."""
    monkeypatch.setenv("DREAM_ITEM_RETENTION_DAYS", "not-a-number")
    caplog.set_level(logging.WARNING, logger="memeval.dreaming.worker")
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 1))).run()
    assert result["counts"]["retention_seconds_effective"] == 30 * _DAY
    assert any("not an integer" in rec.getMessage().lower() for rec in caplog.records)


def test_ttl_default_retention_prunes_31_day_item(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H-TTL-5 (v2) — untyped items past Memory's 30-day retention are pruned;
    fresh ones are not. Replaces v1's env-var-tuned 1-day variant per
    ADR-028 §1 (env is kill-switch only; per-type retention is code-level).
    """
    monkeypatch.delenv("DREAM_ITEM_RETENTION_DAYS", raising=False)
    store = _seed(_FIXED_NOW, ("expired-old", "x", 31), ("fresh", "y", 0))
    result = worker.DreamingWorker(store).run()
    assert result["counts"]["retention_seconds_effective"] == 30 * _DAY
    assert "expired-old" in result["pruned"]["item_ids"]
    assert "fresh" not in result["pruned"]["item_ids"]
    assert "fresh" not in result["pruned"]["item_ids"]


def test_ttl_huge_retention_env_is_ignored(memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """H-TTL-6 (v2) — any non-zero `DREAM_ITEM_RETENTION_DAYS` value is
    IGNORED. Old-age (>30d) untyped items still get pruned via Memory's
    code-level retention; the env value plays no role in tuning. Replaces
    v1's "huge retention prunes nothing" test per ADR-028 §1.
    """
    monkeypatch.setenv("DREAM_ITEM_RETENTION_DAYS", "100000")
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 60), ("b", "y", 200))).run()
    # Both items past Memory's 30-day retention — env was ignored.
    assert result["counts"]["items_pruned"] == 2


# --------------------------------------------------------------------------- #
# §I — Observability
# --------------------------------------------------------------------------- #


def test_ttl_run_emits_exactly_one_summary_event(memory_store_dir: Path, spy_emit: list) -> None:
    """I1 — exactly one dream.summary event per run."""
    worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 60), ("b", "y", 1))).run()
    summary_events = [e for e in spy_emit if e[0] == "dream.summary"]
    assert len(summary_events) == 1


def test_ttl_emit_event_required_fields_extended(memory_store_dir: Path, spy_emit: list) -> None:
    """I2 — emit kwargs include all 6: mode/total_items/duplicate_clusters/items_retired/items_pruned/retention_seconds_effective."""
    worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 60), ("b", "y", 1))).run()
    summary = [e for e in spy_emit if e[0] == "dream.summary"][0]
    kwargs = summary[1]
    for key in ("mode", "total_items", "duplicate_clusters", "items_retired", "items_pruned", "retention_seconds_effective"):
        assert key in kwargs


def test_ttl_emit_event_values_match_summary_extended(memory_store_dir: Path, spy_emit: list) -> None:
    """I3 — emit kwargs match returned dict's fields across all 6 required fields."""
    result = worker.DreamingWorker(_seed(_FIXED_NOW, ("a", "x", 60), ("b", "y", 1))).run()
    summary = [e for e in spy_emit if e[0] == "dream.summary"][0]
    kwargs = summary[1]
    assert kwargs["mode"] == result["mode"]
    assert kwargs["total_items"] == result["counts"]["total_items"]
    assert kwargs["duplicate_clusters"] == result["counts"]["duplicate_clusters"]
    assert kwargs["items_retired"] == result["counts"]["items_retired"]
    assert kwargs["items_pruned"] == result["counts"]["items_pruned"]
    assert kwargs["retention_seconds_effective"] == result["counts"]["retention_seconds_effective"]


def test_ttl_all_deletes_complete_before_summary_emit(memory_store_dir: Path, spy_emit: list) -> None:
    """F-TTL-13 — all delete calls complete before dream.summary emit (monotonic_ns)."""
    store = _seed(_FIXED_NOW, ("s1", "x", 60), ("a", "y", 1), ("b", "y", 2))
    completions: list[int] = []
    real_delete = store.delete

    def _spy_delete(item_id):
        """Record delete completion in monotonic_ns."""
        result = real_delete(item_id)
        completions.append(time.monotonic_ns())
        return result

    store.delete = _spy_delete  # type: ignore[method-assign]

    # Re-patch emit with timestamp
    emit_times: list[int] = []
    original_emit = worker.emit

    def _ts_emit(event_type: str, **fields: Any) -> None:
        """Spy emit recording monotonic_ns at call time."""
        if event_type == "dream.summary":
            emit_times.append(time.monotonic_ns())
        original_emit(event_type, **fields)

    worker.emit = _ts_emit  # type: ignore[assignment]
    try:
        worker.DreamingWorker(store).run()
    finally:
        worker.emit = original_emit  # type: ignore[assignment]

    assert max(completions) <= emit_times[0]


# --------------------------------------------------------------------------- #
# Integration smoke against real RouterStore — beyond rubric scope
# --------------------------------------------------------------------------- #


def test_ttl_real_routerstore_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration — Job 4 TTL pruning end-to-end via real RouterStore + Router.delete fan-out.

    Mirrors the Job-1 integration smoke test but exercises the TTL path:
    a stale item is dropped via Router.delete and disappears from the on-disk
    markdown backend.
    """
    monkeypatch.setenv("MEMORY_PROFILE", "fusion")
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.setenv("MEMORY_STORE", str(tmp_path))
    # ADR-028 §1 — env var is kill-switch-only in v2; non-zero values ignored.
    # Untyped items use the Memory-type retention (30 days) for TTL.
    monkeypatch.delenv("DREAM_ITEM_RETENTION_DAYS", raising=False)

    fixed_now = 2_000_000_000.0
    monkeypatch.setattr("memeval.dreaming.worker._now", lambda: fixed_now)

    from cookbook_memory.core.contract import build_store

    store = build_store(str(tmp_path))

    stale_id = "stale-doc"
    fresh_id = "fresh-doc"
    # 31 days > Memory's 30-day retention (was 30 in v1 when env tuned this
    # to 7; the in-test stale age is bumped to clear the v2 default cleanly).
    store.write(MemoryItem(item_id=stale_id, content="stale content", timestamp=fixed_now - 31 * _DAY))
    store.write(MemoryItem(item_id=fresh_id, content="fresh content", timestamp=fixed_now - 1 * _DAY))

    md_root = tmp_path / "markdown" / "memory"
    stale_md = md_root / f"{stale_id}.md"
    fresh_md = md_root / f"{fresh_id}.md"
    assert stale_md.exists()
    assert fresh_md.exists()

    result = worker.DreamingWorker(store).run()

    assert stale_id in result["pruned"]["item_ids"]
    assert fresh_id not in result["pruned"]["item_ids"]
    assert store.get(stale_id) is None
    assert store.get(fresh_id) is not None
    assert not stale_md.exists(), "TTL fan-out must unlink stale markdown file"
    assert fresh_md.exists(), "fresh item's markdown must persist"


# --------------------------------------------------------------------------- #
# Missing-test backfill per jasnah final grade (FAIL → fixes)
# --------------------------------------------------------------------------- #


def test_ttl_survivors_untouched(memory_store_dir: Path) -> None:
    """F-TTL-9 — survivors (not pruned, not retired) have byte-identical content/relevancy/version/timestamp."""
    store = _DeleteAwareStore()
    store.write(MemoryItem(item_id="stale", content="old", timestamp=_FIXED_NOW - 60 * _DAY, relevancy=0.3))
    store.write(MemoryItem(item_id="survivor", content="fresh content", timestamp=_FIXED_NOW - 1 * _DAY, relevancy=0.9))
    pre = store.get("survivor")
    pre_snapshot = (pre.content, pre.relevancy, pre.version, pre.timestamp)
    worker.DreamingWorker(store).run()
    post = store.get("survivor")
    assert post is not None
    assert (post.content, post.relevancy, post.version, post.timestamp) == pre_snapshot


def test_ttl_trajectories_path_truthy_raises_before_ttl_pass(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G1 — ValueError raised + _basedir_dream_lock not entered + worker._now not called."""
    now_calls: list[float] = []
    monkeypatch.setattr("memeval.dreaming.worker._now", lambda: (now_calls.append(0.0) or _FIXED_NOW))
    lock_entered: list[bool] = []
    original_lock = _state._basedir_dream_lock
    from contextlib import contextmanager

    @contextmanager
    def _spy_lock(basedir):
        """Record any basedir-lock entry — the trajectories guard must raise BEFORE this fires."""
        lock_entered.append(True)
        with original_lock(basedir):
            yield

    monkeypatch.setattr("memeval.dreaming.worker._basedir_dream_lock", _spy_lock)
    store = _seed(_FIXED_NOW, ("a", "x", 1))
    with pytest.raises(ValueError):
        worker.DreamingWorker(store).run(trajectories_path="/bogus")
    assert lock_entered == []
    assert now_calls == []


def test_ttl_env_kill_switch_read_per_run(memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """H-TTL-6 (v2) — DREAM_ITEM_RETENTION_DAYS is read from os.environ on
    every run() (not cached). Per ADR-028 §1, the only operationally
    meaningful value is `"0"` (kill-switch). The summary field reports
    Memory-type retention regardless; the difference between runs is
    whether prunes happen at all.
    """
    monkeypatch.delenv("DREAM_ITEM_RETENTION_DAYS", raising=False)
    store = _seed(_FIXED_NOW, ("expired", "x", 31))
    first = worker.DreamingWorker(store).run()
    monkeypatch.setenv("DREAM_ITEM_RETENTION_DAYS", "0")
    second = worker.DreamingWorker(store).run()
    # Both report the Memory-type retention in the summary field (the field
    # is descriptive of the default, not whether the pass ran).
    assert first["counts"]["retention_seconds_effective"] == 30 * _DAY
    assert second["counts"]["retention_seconds_effective"] == 30 * _DAY
    # But the kill-switch suppressed the second run's deletes — the items
    # come back as an empty pruned list, NOT because the store re-grew but
    # because the worker noticed `DREAM_ITEM_RETENTION_DAYS=0` and skipped.
    assert second["counts"]["items_pruned"] == 0


def test_ttl_now_callable_exists_and_monkeypatchable(monkeypatch: pytest.MonkeyPatch) -> None:
    """J-TTL-1 — _now is module-level + callable + monkeypatchable to a stub."""
    from memeval.dreaming.worker import _now as imported_now
    assert callable(imported_now)
    monkeypatch.setattr("memeval.dreaming.worker._now", lambda: 42.0)
    from memeval.dreaming import worker as _w
    assert _w._now() == 42.0


def test_ttl_delete_called_with_single_id_arg(memory_store_dir: Path) -> None:
    """K6 — self.store.delete called with exactly one positional arg, no kwargs."""
    store = _seed(_FIXED_NOW, ("stale", "x", 60), ("a", "y", 1), ("b", "y", 2))
    spy = MagicMock(wraps=store.delete)
    store.delete = spy  # type: ignore[method-assign]
    worker.DreamingWorker(store).run()
    for call in spy.call_args_list:
        assert len(call.args) == 1
        assert call.kwargs == {}


def test_ttl_preserves_lock_contended_event(
    memory_store_dir: Path, spy_emit: list,
) -> None:
    """I4 — dream.lock_contended event still emits on basedir-lock contention."""
    from memeval.dreaming._state import _basedir_dream_lock as raw_lock
    with raw_lock(memory_store_dir):
        with pytest.raises(_DreamLockHeld):
            with raw_lock(memory_store_dir):
                pass
    assert any(e[0] == "dream.lock_contended" for e in spy_emit)


def test_ttl_preserves_unsupported_fs_event(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I4 — _UnsupportedFsError still raised on NFS detection (CLI emits dream.unsupported_fs)."""
    monkeypatch.setattr("memeval.dreaming.worker._is_network_fs", lambda path: True)
    monkeypatch.delenv("DREAM_ALLOW_NETWORK_FS", raising=False)
    store = _seed(_FIXED_NOW, ("a", "x", 1))
    with pytest.raises(_UnsupportedFsError):
        worker.DreamingWorker(store).run()


def test_ttl_preserves_daydream_dream_in_progress_skipped_event(
    memory_store_dir: Path, spy_emit: list, tmp_path: Path,
) -> None:
    """I4 — Daydream still emits daydream.dream_in_progress_skipped when Dream sweep holds the basedir lock."""
    from memeval.dreaming import engine
    from memeval.dreaming._state import _basedir_dream_lock as raw_lock
    log_path = tmp_path / "log.jsonl"
    log_path.write_text("content\n")
    with raw_lock(memory_store_dir):
        engine.daydream(
            session_id="sess",
            log_path=log_path,
            store=_DeleteAwareStore(),
            basedir=memory_store_dir,
            client=MagicMock(),
        )
    assert any(e[0] == "daydream.dream_in_progress_skipped" for e in spy_emit)


def test_ttl_preserves_daydream_happy_path_event_surface(
    memory_store_dir: Path, spy_emit: list, tmp_path: Path,
) -> None:
    """I4 — Daydream happy-path emits no new dream.* family events."""
    from memeval.dreaming import engine
    log_path = tmp_path / "log.jsonl"
    log_path.write_text("content\n")
    engine.daydream(
        session_id="sess",
        log_path=log_path,
        store=_DeleteAwareStore(),
        basedir=memory_store_dir,
        client=MagicMock(),
    )
    forbidden = {"dream.lock_contended", "dream.unsupported_fs"}
    emitted = {e[0] for e in spy_emit}
    assert forbidden.isdisjoint(emitted)


def test_ttl_inherits_job1_lock_and_nfs_surface(memory_store_dir: Path) -> None:
    """L1 — Job 1's basedir-lock + NFS surface still holds under the TTL-extended worker."""
    # Sanity: the four named primitives + classes still exist in _state and have
    # the same identity expected by Job 1 §L. (Functional re-run of Job 1 §L
    # happens via the existing test_worker_mutation.py suite — running both
    # files in CI catches any regression.)
    assert _DreamLockHeld is not None
    assert _UnsupportedFsError is not None
    assert callable(_state._basedir_dream_lock)
    assert callable(_state._is_network_fs)
    # Lock acquisition + release cycle works post-Job-4.
    with _state._basedir_dream_lock(memory_store_dir):
        pass
    # Second acquisition succeeds after release.
    with _state._basedir_dream_lock(memory_store_dir):
        pass


def test_ttl_prune_pass_inside_basedir_lock(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L2 — every TTL delete completion is between basedir-lock acquire and release."""
    order: list[tuple[str, int]] = []
    original_lock = _state._basedir_dream_lock
    from contextlib import contextmanager

    @contextmanager
    def _trace_lock(basedir):
        """Record lock acquire/release timestamps."""
        order.append(("lock_acquire", time.monotonic_ns()))
        with original_lock(basedir):
            yield
        order.append(("lock_release", time.monotonic_ns()))

    monkeypatch.setattr("memeval.dreaming.worker._basedir_dream_lock", _trace_lock)
    store = _seed(_FIXED_NOW, ("s1", "x", 60), ("s2", "y", 60))
    real_delete = store.delete

    def _spy_delete(item_id):
        """Record TTL-path delete completion timestamps."""
        result = real_delete(item_id)
        order.append(("delete_complete", time.monotonic_ns()))
        return result

    store.delete = _spy_delete  # type: ignore[method-assign]
    worker.DreamingWorker(store).run()
    acquire_ts = next(ts for kind, ts in order if kind == "lock_acquire")
    release_ts = next(ts for kind, ts in order if kind == "lock_release")
    delete_ts = [ts for kind, ts in order if kind == "delete_complete"]
    assert delete_ts, "test fixture failed to fire any deletes"
    for ts in delete_ts:
        assert acquire_ts <= ts <= release_ts


def test_ttl_nfs_short_circuits_before_ttl(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L3 — NFS hard-fail short-circuits before TTL: _now uncalled, store.delete uncalled."""
    monkeypatch.setattr("memeval.dreaming.worker._is_network_fs", lambda path: True)
    monkeypatch.delenv("DREAM_ALLOW_NETWORK_FS", raising=False)
    now_calls: list[float] = []
    monkeypatch.setattr("memeval.dreaming.worker._now", lambda: (now_calls.append(0.0) or _FIXED_NOW))
    store = _seed(_FIXED_NOW, ("stale", "x", 60))
    delete_spy = MagicMock(wraps=store.delete)
    store.delete = delete_spy  # type: ignore[method-assign]
    with pytest.raises(_UnsupportedFsError):
        worker.DreamingWorker(store).run()
    assert now_calls == []
    assert delete_spy.call_count == 0


def test_ttl_two_concurrent_workers_only_one_mutates(memory_store_dir: Path) -> None:
    """M1 — two workers in two threads: only one acquires the basedir lock and runs both passes."""
    import threading

    store = _seed(_FIXED_NOW, ("stale", "x", 60), ("a", "y", 1), ("b", "y", 2))
    delete_counts: list[int] = []
    real_delete = store.delete
    lock_guard = threading.Lock()

    def _counting_delete(item_id):
        """Count successful retirements across both threads."""
        with lock_guard:
            delete_counts.append(1)
        return real_delete(item_id)

    store.delete = _counting_delete  # type: ignore[method-assign]
    exceptions: list[Exception] = []

    def _runner():
        """Run the worker; record any exception so the main thread can see it."""
        try:
            worker.DreamingWorker(store).run()
        except _DreamLockHeld as e:
            exceptions.append(e)
        except Exception as e:
            exceptions.append(e)

    t1 = threading.Thread(target=_runner)
    t2 = threading.Thread(target=_runner)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    # Exactly 2 deletes total across both threads (1 TTL + 1 dedup loser).
    assert sum(delete_counts) == 2


def test_daydream_skips_while_dream_ttl_running(
    memory_store_dir: Path, spy_emit: list, tmp_path: Path,
) -> None:
    """M2 — Daydream invoked while Dream's TTL pass is in progress skips with daydream.dream_in_progress_skipped."""
    from memeval.dreaming import engine
    from memeval.dreaming._state import _basedir_dream_lock as raw_lock
    log_path = tmp_path / "log.jsonl"
    log_path.write_text("content\n")
    # Simulate Dream holding the lock during its TTL pass.
    with raw_lock(memory_store_dir):
        engine.daydream(
            session_id="sess",
            log_path=log_path,
            store=_DeleteAwareStore(),
            basedir=memory_store_dir,
            client=MagicMock(),
        )
    assert any(e[0] == "daydream.dream_in_progress_skipped" for e in spy_emit)


# --------------------------------------------------------------------------- #
# §K — ADR-dreaming-028 §1 per-type retention contract
# --------------------------------------------------------------------------- #

def test_ttl_durable_types_never_age_out_of_store(memory_store_dir: Path) -> None:
    """ADR-028 §1 — items with a "durable" `okf_type` (Identity, Convention,
    Invariant, Workaround, Bug, Contradiction) are NEVER pruned by the TTL
    pass, no matter how old. Sets timestamps 10 years in the past and
    asserts no pruning.
    """
    from memeval.dreaming.worker import TYPE_RETENTION_DAYS

    durable = [t for t, days in TYPE_RETENTION_DAYS.items() if days is None]
    assert durable, "TYPE_RETENTION_DAYS must include at least one durable type"

    store = _DeleteAwareStore()
    ancient_ts = _FIXED_NOW - 365 * 10 * _DAY  # 10 years old
    for okf_type in durable:
        store.write(MemoryItem(
            item_id=f"durable-{okf_type.lower()}",
            content=f"a {okf_type} card from a decade ago",
            timestamp=ancient_ts,
            metadata={"okf_type": okf_type},
        ))

    result = worker.DreamingWorker(store).run()
    assert result["pruned"]["item_ids"] == [], (
        f"durable types pruned: {result['pruned']['item_ids']}"
    )


def test_ttl_calendar_decay_types_age_at_their_per_type_window(memory_store_dir: Path) -> None:
    """ADR-028 §1 — `Fix` (90d), `Preference` (180d), `Decision` (365d), and
    `Memory` fallback (30d) each age at their type-specific retention.
    Items just past their type's window are pruned; items just inside are not.
    """
    from memeval.dreaming.worker import TYPE_RETENTION_DAYS

    store = _DeleteAwareStore()
    for okf_type in ("Fix", "Preference", "Decision", "Memory"):
        days = TYPE_RETENTION_DAYS[okf_type]
        assert days is not None
        # Just past the window — should prune.
        store.write(MemoryItem(
            item_id=f"old-{okf_type.lower()}",
            content=f"old {okf_type}",
            timestamp=_FIXED_NOW - (days + 1) * _DAY,
            metadata={"okf_type": okf_type},
        ))
        # Just inside the window — should survive.
        store.write(MemoryItem(
            item_id=f"young-{okf_type.lower()}",
            content=f"young {okf_type}",
            timestamp=_FIXED_NOW - (days - 1) * _DAY,
            metadata={"okf_type": okf_type},
        ))

    pruned = set(worker.DreamingWorker(store).run()["pruned"]["item_ids"])
    assert pruned == {"old-fix", "old-preference", "old-decision", "old-memory"}


def test_ttl_unknown_okf_type_falls_back_to_memory_retention(memory_store_dir: Path) -> None:
    """ADR-028 §1 — an item with `metadata.okf_type` set to a value NOT in
    `TYPE_RETENTION_DAYS` uses the default (Memory's 30-day retention).
    Defensive against future taxonomy drift where a new value lands in
    `OKF_CONTENT_TYPES` without a corresponding `TYPE_RETENTION_DAYS` entry.
    """
    store = _DeleteAwareStore()
    store.write(MemoryItem(
        item_id="unknown-31d", content="x",
        timestamp=_FIXED_NOW - 31 * _DAY,
        metadata={"okf_type": "SomeUnknownType"},
    ))
    store.write(MemoryItem(
        item_id="unknown-29d", content="y",
        timestamp=_FIXED_NOW - 29 * _DAY,
        metadata={"okf_type": "SomeUnknownType"},
    ))
    pruned = set(worker.DreamingWorker(store).run()["pruned"]["item_ids"])
    assert pruned == {"unknown-31d"}


def test_ttl_missing_okf_type_metadata_defaults_to_memory(memory_store_dir: Path) -> None:
    """ADR-028 §1 — pre-V5 items with no `metadata.okf_type` field at all
    use the Memory-type retention. Back-compat: all existing stores have
    untyped memories; nothing about their TTL behavior should change.
    """
    store = _DeleteAwareStore()
    # No metadata at all — older parser pre-ADR-027 didn't set okf_type.
    store.write(MemoryItem(
        item_id="legacy-31d", content="x",
        timestamp=_FIXED_NOW - 31 * _DAY,
    ))
    store.write(MemoryItem(
        item_id="legacy-29d", content="y",
        timestamp=_FIXED_NOW - 29 * _DAY,
    ))
    pruned = set(worker.DreamingWorker(store).run()["pruned"]["item_ids"])
    assert pruned == {"legacy-31d"}


def test_ttl_retention_table_covers_every_taxonomy_value() -> None:
    """ADR-028 §1 contract — every value in `OKF_CONTENT_TYPES` MUST have a
    `TYPE_RETENTION_DAYS` entry. Plus `Memory` (the parser fallback). Catches
    silent drift: if a new value lands in the taxonomy without a retention
    decision, this test fails loudly.
    """
    from memeval.dreaming.prompts import OKF_CONTENT_TYPES
    from memeval.dreaming.worker import TYPE_RETENTION_DAYS

    missing = (OKF_CONTENT_TYPES | {"Memory"}) - set(TYPE_RETENTION_DAYS)
    assert not missing, (
        f"TYPE_RETENTION_DAYS missing entries for: {sorted(missing)} — "
        "adding a value to OKF_CONTENT_TYPES requires a retention decision."
    )


# --------------------------------------------------------------------------- #
# §L — ADR-dreaming-028 §2 `iter_pages` protocol surface
# --------------------------------------------------------------------------- #

def test_iter_store_pages_default_falls_back_to_single_page_from_all() -> None:
    """ADR-028 §2 — backends without `iter_pages()` get a single page wrapping
    `store.all()`. Byte-identical to today's read pattern; v1-contract intact.
    """
    from memeval.dreaming.worker import _iter_store_pages

    store = _DeleteAwareStore()
    store.write(MemoryItem(item_id="a", content="alpha", timestamp=_FIXED_NOW))
    store.write(MemoryItem(item_id="b", content="beta", timestamp=_FIXED_NOW))
    store.write(MemoryItem(item_id="c", content="gamma", timestamp=_FIXED_NOW))

    pages = list(_iter_store_pages(store))
    assert len(pages) == 1, "default fallback yields exactly one page"
    assert {item.item_id for item in pages[0]} == {"a", "b", "c"}


def test_iter_store_pages_uses_native_iter_pages_when_available() -> None:
    """ADR-028 §2 — backends opting into `iter_pages(page_size)` get their
    native implementation called directly. Helper delegates without
    materializing the page list itself.
    """
    from memeval.dreaming.worker import _iter_store_pages

    captured_calls: list[int] = []

    class _StreamingStore(_DeleteAwareStore):
        def iter_pages(self, *, page_size: int):  # type: ignore[no-untyped-def]
            captured_calls.append(page_size)
            items = list(self._items.values())
            for i in range(0, len(items), page_size):
                yield items[i : i + page_size]

    store = _StreamingStore()
    for i in range(5):
        store.write(MemoryItem(item_id=f"item-{i}", content=f"c{i}", timestamp=_FIXED_NOW))

    pages = list(_iter_store_pages(store, page_size=2))
    assert captured_calls == [2], "page_size kwarg threaded through to override"
    assert len(pages) == 3, "5 items at page_size=2 → 3 pages (2 + 2 + 1)"
    assert [len(p) for p in pages] == [2, 2, 1]
    # Total content preserved across pages.
    assert sum(len(p) for p in pages) == 5


def test_iter_store_pages_default_page_size_is_used_when_unspecified() -> None:
    """ADR-028 §2 — when the caller doesn't pass `page_size`, the helper
    threads the module-level `_DEFAULT_PAGE_SIZE` through to overriding
    backends. Locks down the call contract so a future refactor that
    moves the default elsewhere doesn't silently change page size at
    every call site.
    """
    from memeval.dreaming.worker import _DEFAULT_PAGE_SIZE, _iter_store_pages

    captured: list[int] = []

    class _CapturingStore(_DeleteAwareStore):
        def iter_pages(self, *, page_size: int):  # type: ignore[no-untyped-def]
            captured.append(page_size)
            yield []  # don't need real items for this assertion

    list(_iter_store_pages(_CapturingStore()))
    assert captured == [_DEFAULT_PAGE_SIZE]


def test_worker_dream_loop_routes_reads_through_iter_pages(memory_store_dir: Path) -> None:
    """ADR-028 §2 wiring — the worker's `dream()` method must use
    `_iter_store_pages()`, NOT a direct `store.all()` call, when reading the
    consolidation working set. Verified by a streaming-store override that
    counts how many times its `iter_pages` is called; today's worker
    materializes the full list per `dream()` invocation, so exactly one
    `iter_pages` call is expected.
    """
    iter_call_count = [0]

    class _CountingStore(_DeleteAwareStore):
        def iter_pages(self, *, page_size: int):  # type: ignore[no-untyped-def]
            iter_call_count[0] += 1
            yield list(self._items.values())

    store = _CountingStore()
    store.write(MemoryItem(
        item_id="m1", content="alpha", timestamp=_FIXED_NOW,
    ))

    worker.DreamingWorker(store).run()
    assert iter_call_count[0] == 1, (
        f"`dream()` should call store.iter_pages exactly once per run "
        f"(today's worker materializes all items); got {iter_call_count[0]}"
    )


# --------------------------------------------------------------------------- #
# §M — ADR-dreaming-028 §2 `_neighborhood_for` helper
# --------------------------------------------------------------------------- #

def test_neighborhood_returns_pivot_first_then_search_results() -> None:
    """ADR-028 §2 — the helper always returns the pivot item as element [0],
    followed by store.search results in store-ranked order."""
    from memeval.dreaming.worker import _neighborhood_for
    from memeval.schema import RetrievedItem

    pivot = MemoryItem(item_id="pivot", content="alpha bug fix", timestamp=_FIXED_NOW)
    n1 = MemoryItem(item_id="n1", content="related-1", timestamp=_FIXED_NOW)
    n2 = MemoryItem(item_id="n2", content="related-2", timestamp=_FIXED_NOW)

    class _StubStore(_DeleteAwareStore):
        def search(self, query, *, k=5, as_of=None, **kwargs):  # type: ignore[override]
            return [RetrievedItem(item=n1, score=0.9, rank=0),
                    RetrievedItem(item=n2, score=0.7, rank=1)]

    result = _neighborhood_for(_StubStore(), pivot, k=10)
    assert result[0].item_id == "pivot"
    assert [r.item_id for r in result[1:]] == ["n1", "n2"]


def test_neighborhood_filters_pivot_from_search_hits() -> None:
    """ADR-028 §2 — some backends return the query item itself as the top
    search hit (semantic backends do this when content is identical to query).
    The helper MUST de-duplicate so the pivot doesn't appear twice."""
    from memeval.dreaming.worker import _neighborhood_for
    from memeval.schema import RetrievedItem

    pivot = MemoryItem(item_id="pivot", content="x", timestamp=_FIXED_NOW)
    other = MemoryItem(item_id="other", content="y", timestamp=_FIXED_NOW)

    class _SelfReturningStore(_DeleteAwareStore):
        def search(self, query, *, k=5, as_of=None, **kwargs):  # type: ignore[override]
            return [RetrievedItem(item=pivot, score=1.0, rank=0),
                    RetrievedItem(item=other, score=0.5, rank=1)]

    result = _neighborhood_for(_SelfReturningStore(), pivot)
    assert [r.item_id for r in result] == ["pivot", "other"], (
        "pivot must appear exactly once, as element [0]"
    )


def test_neighborhood_threads_k_kwarg_to_store_search() -> None:
    """ADR-028 §2 — the `k` arg passes through to `store.search(k=...)` so a
    caller asking for k=20 doesn't get silently clipped to 10."""
    from memeval.dreaming.worker import _neighborhood_for

    captured_ks: list[int] = []

    class _CapturingStore(_DeleteAwareStore):
        def search(self, query, *, k=5, as_of=None, **kwargs):  # type: ignore[override]
            captured_ks.append(k)
            return []

    pivot = MemoryItem(item_id="p", content="x", timestamp=_FIXED_NOW)
    _neighborhood_for(_CapturingStore(), pivot, k=20)
    assert captured_ks == [20]


def test_neighborhood_default_k_is_used_when_unspecified() -> None:
    """ADR-028 §2 — the module-level `_DEFAULT_NEIGHBORHOOD_K` (10 per the
    ADR's Mem0 reference design) is threaded through when callers don't
    specify."""
    from memeval.dreaming.worker import _DEFAULT_NEIGHBORHOOD_K, _neighborhood_for

    captured: list[int] = []

    class _CapturingStore(_DeleteAwareStore):
        def search(self, query, *, k=5, as_of=None, **kwargs):  # type: ignore[override]
            captured.append(k)
            return []

    _neighborhood_for(_CapturingStore(), MemoryItem(item_id="p", content="x", timestamp=_FIXED_NOW))
    assert captured == [_DEFAULT_NEIGHBORHOOD_K]


def test_neighborhood_empty_search_returns_pivot_alone() -> None:
    """ADR-028 §2 — backends with no matches return the pivot alone. Callers
    detect a thin neighborhood by `len(result) == 1` and can skip per-item
    consolidation for that pivot."""
    from memeval.dreaming.worker import _neighborhood_for

    class _EmptyStore(_DeleteAwareStore):
        def search(self, query, *, k=5, as_of=None, **kwargs):  # type: ignore[override]
            return []

    pivot = MemoryItem(item_id="p", content="x", timestamp=_FIXED_NOW)
    assert _neighborhood_for(_EmptyStore(), pivot) == [pivot]


def test_neighborhood_search_failure_fails_open_to_pivot_alone() -> None:
    """ADR-028 §2 + ADR-harness-006 — a search backend that raises (e.g.,
    missing Voyage API key on the accuracy profile) MUST NOT crash the
    consolidation pass. The caller gets the pivot alone — same as if the
    backend had returned no neighbors."""
    from memeval.dreaming.worker import _neighborhood_for

    class _FailingStore(_DeleteAwareStore):
        def search(self, query, *, k=5, as_of=None, **kwargs):  # type: ignore[override]
            raise RuntimeError("Voyage API error (HTTP 429)")

    pivot = MemoryItem(item_id="p", content="x", timestamp=_FIXED_NOW)
    assert _neighborhood_for(_FailingStore(), pivot) == [pivot]


# --------------------------------------------------------------------------- #
# §N — ADR-dreaming-028 §2 `_detect_contradictions_neighborhood` helper
# --------------------------------------------------------------------------- #

class _StubCompletion:
    """Mimic `llm.Completion` shape used by the contradiction caller."""
    def __init__(self, text: str, tokens_in: int = 100, tokens_out: int = 50) -> None:
        self.text = text
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out


class _StubLLM:
    """LLM client stub for contradiction tests. `responses` queues responses
    that `.complete()` returns in order; `calls` records each invocation."""
    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.model = "stub-model"

    def complete(self, user_message, *, system=None, max_tokens=None):
        self.calls.append({"user": user_message, "system": system, "max_tokens": max_tokens})
        if not self.responses:
            return _StubCompletion("", tokens_in=0, tokens_out=0)
        return self.responses.pop(0)


class _StubSearchStore(_DeleteAwareStore):
    """Store stub returning a fixed neighborhood when `search(query)` is called.

    `lookup`: maps a content string (the query the helper will send) to the
    list of neighbor items returned. Items from every list are also written
    to the underlying store so item_id lookups work."""
    def __init__(self, lookup: dict[str, list[MemoryItem]]) -> None:
        super().__init__()
        seen: set[str] = set()
        for neighbors in lookup.values():
            for it in neighbors:
                if it.item_id not in seen:
                    self.write(it)
                    seen.add(it.item_id)
        self._lookup = lookup

    def search(self, query, *, k=5, as_of=None, **kwargs):  # type: ignore[override]
        from memeval.schema import RetrievedItem
        neighbors = self._lookup.get(query, [])
        return [
            RetrievedItem(item=n, score=1.0 - i * 0.1, rank=i)
            for i, n in enumerate(neighbors[:k])
        ]


def test_neighborhood_contradiction_returns_empty_for_empty_items(memory_store_dir: Path) -> None:
    """ADR-028 §2 — empty items list short-circuits with empty result, no LLM call."""
    from memeval.dreaming.worker import _detect_contradictions_neighborhood

    llm = _StubLLM(responses=[])
    result = _detect_contradictions_neighborhood(
        items=[], store=_DeleteAwareStore(), client=llm,
        max_calls=10, model="stub", session_id="s1", now=_FIXED_NOW,
    )
    assert result.pairs == []
    assert result.llm_calls == 0
    assert llm.calls == []


def test_neighborhood_contradiction_respects_max_calls_zero(memory_store_dir: Path) -> None:
    """ADR-028 §2 — max_calls=0 (disabled-pass) returns empty result, no LLM."""
    from memeval.dreaming.worker import _detect_contradictions_neighborhood

    pivot = MemoryItem(item_id="p", content="x", timestamp=_FIXED_NOW)
    llm = _StubLLM(responses=[])
    result = _detect_contradictions_neighborhood(
        items=[pivot], store=_DeleteAwareStore(), client=llm,
        max_calls=0, model="stub", session_id="s1", now=_FIXED_NOW,
    )
    assert result.pairs == []
    assert llm.calls == []


def test_neighborhood_contradiction_skips_pivots_with_empty_neighborhoods(memory_store_dir: Path) -> None:
    """ADR-028 §2 — pivots whose neighborhood is `[pivot]` alone (no neighbors)
    are skipped without burning an LLM call. Saves budget for productive pivots."""
    from memeval.dreaming.worker import _detect_contradictions_neighborhood

    p1 = MemoryItem(item_id="p1", content="a", timestamp=_FIXED_NOW)
    p2 = MemoryItem(item_id="p2", content="b", timestamp=_FIXED_NOW)
    n1 = MemoryItem(item_id="n1", content="c", timestamp=_FIXED_NOW)
    store = _StubSearchStore(lookup={"b": [n1]})  # p1 has no neighbors

    llm = _StubLLM(responses=[_StubCompletion('{"pairs": []}')])
    result = _detect_contradictions_neighborhood(
        items=[p1, p2], store=store, client=llm,
        max_calls=10, model="stub", session_id="s1", now=_FIXED_NOW,
    )
    assert result.llm_calls == 1, "only p2's neighborhood should have triggered a call"


def test_neighborhood_contradiction_finds_pair_within_neighborhood(memory_store_dir: Path) -> None:
    """ADR-028 §2 — LLM identifies a contradicting pair within the pivot's
    neighborhood; helper returns it after deterministic loser selection."""
    from memeval.dreaming.worker import _detect_contradictions_neighborhood

    pivot = MemoryItem(item_id="p", content="prefers tabs", timestamp=_FIXED_NOW)
    nbr = MemoryItem(item_id="n", content="prefers spaces", timestamp=_FIXED_NOW - _DAY)
    store = _StubSearchStore(lookup={"prefers tabs": [nbr]})

    llm_response = _StubCompletion('{"pairs": [{"a_id": "p", "b_id": "n", "rationale": "disagree on tabs/spaces"}]}')
    llm = _StubLLM(responses=[llm_response])
    result = _detect_contradictions_neighborhood(
        items=[pivot], store=store, client=llm,
        max_calls=10, model="stub", session_id="s1", now=_FIXED_NOW,
    )
    assert len(result.pairs) == 1
    pair = result.pairs[0]
    # `p` is newer (timestamp=_FIXED_NOW) → wins; `n` (older) → loser.
    assert pair.winner_id == "p"
    assert pair.loser_id == "n"
    assert "tabs/spaces" in pair.rationale


def test_neighborhood_contradiction_dedupes_pair_surfaced_from_two_pivots(memory_store_dir: Path) -> None:
    """ADR-028 §2 — if pivot A's neighborhood and pivot B's neighborhood both
    surface the (A, B) pair, the dedup keeps the first occurrence and skips
    the second. Critical for not double-counting cost or producing duplicate
    delete plans."""
    from memeval.dreaming.worker import _detect_contradictions_neighborhood

    a = MemoryItem(item_id="A", content="a-content", timestamp=_FIXED_NOW)
    b = MemoryItem(item_id="B", content="b-content", timestamp=_FIXED_NOW - _DAY)
    store = _StubSearchStore(lookup={"a-content": [b], "b-content": [a]})

    # Both LLM calls return the same (A, B) pair.
    resp_a = _StubCompletion('{"pairs": [{"a_id": "A", "b_id": "B", "rationale": "same disagreement"}]}')
    resp_b = _StubCompletion('{"pairs": [{"a_id": "B", "b_id": "A", "rationale": "still the same disagreement"}]}')
    llm = _StubLLM(responses=[resp_a, resp_b])
    result = _detect_contradictions_neighborhood(
        items=[a, b], store=store, client=llm,
        max_calls=10, model="stub", session_id="s1", now=_FIXED_NOW,
    )
    assert len(result.pairs) == 1, "the same pair must not be returned twice"
    assert result.llm_calls == 2, "both pivots judged, but dedup happens AFTER the LLM call"


def test_neighborhood_contradiction_bounds_pivots_by_max_calls(memory_store_dir: Path) -> None:
    """ADR-028 §2 — max_calls caps PIVOT count, not arbitrary batch count.
    After max_calls successful LLM calls, no more pivots are processed."""
    from memeval.dreaming.worker import _detect_contradictions_neighborhood

    items = [
        MemoryItem(item_id=f"p{i}", content=f"c{i}", timestamp=_FIXED_NOW)
        for i in range(5)
    ]
    nbr = MemoryItem(item_id="nbr", content="ny", timestamp=_FIXED_NOW)
    store = _StubSearchStore(lookup={p.content: [nbr] for p in items})

    # Always return empty pairs; just count calls.
    llm = _StubLLM(responses=[_StubCompletion('{"pairs": []}') for _ in range(10)])
    result = _detect_contradictions_neighborhood(
        items=items, store=store, client=llm,
        max_calls=3, model="stub", session_id="s1", now=_FIXED_NOW,
    )
    assert llm.calls.__len__() == 3, f"max_calls=3 must cap pivots judged; got {len(llm.calls)} calls"
    assert result.llm_calls == 3


def test_neighborhood_contradiction_drops_pair_with_protected_loser(memory_store_dir: Path) -> None:
    """ADR-028 §2 — pair whose `loser_id` is in `protected_ids` (e.g. a prior
    dedup-cluster winner) is dropped with the same `contradiction_pair_dropped_winner_collision`
    event the v1 path emits. Conservative posture per halliday B5 + CodeRabbit #105."""
    from memeval.dreaming.worker import _detect_contradictions_neighborhood

    p = MemoryItem(item_id="p", content="x", timestamp=_FIXED_NOW)
    n = MemoryItem(item_id="n", content="y", timestamp=_FIXED_NOW - _DAY)
    store = _StubSearchStore(lookup={"x": [n]})

    llm = _StubLLM(responses=[_StubCompletion('{"pairs": [{"a_id": "p", "b_id": "n", "rationale": "r"}]}')])
    # Protect the loser (older `n`); the pair should be dropped.
    result = _detect_contradictions_neighborhood(
        items=[p], store=store, client=llm,
        max_calls=10, model="stub", session_id="s1", now=_FIXED_NOW,
        protected_ids={"n"},
    )
    assert result.pairs == [], "pair with protected loser must be dropped"


def test_neighborhood_contradiction_fails_open_on_llm_exception(memory_store_dir: Path) -> None:
    """ADR-028 §2 + ADR-harness-006 — an LLM client that raises (Voyage 429,
    network timeout, etc.) MUST NOT crash the consolidation pass. The
    affected pivot is skipped via the `dream.contradiction_skipped_unavailable_llm`
    event; the helper continues with the next pivot."""
    from memeval.dreaming.worker import _detect_contradictions_neighborhood

    p1 = MemoryItem(item_id="p1", content="x", timestamp=_FIXED_NOW)
    p2 = MemoryItem(item_id="p2", content="y", timestamp=_FIXED_NOW)
    n = MemoryItem(item_id="nbr", content="z", timestamp=_FIXED_NOW)
    store = _StubSearchStore(lookup={"x": [n], "y": [n]})

    class _RaisingLLM(_StubLLM):
        def complete(self, *args, **kwargs):
            self.calls.append({})
            raise RuntimeError("Voyage API error (HTTP 429)")

    llm = _RaisingLLM(responses=[])
    result = _detect_contradictions_neighborhood(
        items=[p1, p2], store=store, client=llm,
        max_calls=10, model="stub", session_id="s1", now=_FIXED_NOW,
    )
    # No pairs returned, but both pivots attempted (and both burned a call
    # via fail-open). No exception propagates to the caller.
    assert result.pairs == []
    assert result.llm_calls == 2


# --------------------------------------------------------------------------- #
# §O — ADR-dreaming-028 §2 PR #2d feature-flag routing
# --------------------------------------------------------------------------- #

def test_use_neighborhood_contradiction_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-028 §2 PR #2d — default unset → False (v1 batch path)."""
    from memeval.dreaming.worker import _read_use_neighborhood_contradiction
    monkeypatch.delenv("DREAM_CONTRADICTION_NEIGHBORHOOD", raising=False)
    assert _read_use_neighborhood_contradiction() is False


def test_use_neighborhood_contradiction_flag_one_enables(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-028 §2 PR #2d — exactly `"1"` enables the v2 neighborhood path."""
    from memeval.dreaming.worker import _read_use_neighborhood_contradiction
    monkeypatch.setenv("DREAM_CONTRADICTION_NEIGHBORHOOD", "1")
    assert _read_use_neighborhood_contradiction() is True


def test_use_neighborhood_contradiction_other_values_read_as_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-028 §2 PR #2d — strict equality with `"1"` only. "true", "yes",
    misspellings, "0" etc. all read as False. Keeps the flag deliberate;
    an operator typing the wrong word doesn't silently flip consolidation."""
    from memeval.dreaming.worker import _read_use_neighborhood_contradiction
    for val in ("0", "", "true", "True", "yes", "on", "TRUE", " 1 ", "1 "):
        monkeypatch.setenv("DREAM_CONTRADICTION_NEIGHBORHOOD", val)
        assert _read_use_neighborhood_contradiction() is False, (
            f"DREAM_CONTRADICTION_NEIGHBORHOOD={val!r} must read as off"
        )


def test_dream_routes_to_v1_path_when_flag_off(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-028 §2 PR #2d — when flag is off (default), `dream()` calls
    `_detect_contradictions` (v1) NOT `_detect_contradictions_neighborhood`."""
    from memeval.dreaming import worker as worker_mod

    monkeypatch.delenv("DREAM_CONTRADICTION_NEIGHBORHOOD", raising=False)

    v1_called = [0]
    v2_called = [0]
    orig_v1 = worker_mod._detect_contradictions
    orig_v2 = worker_mod._detect_contradictions_neighborhood

    def spy_v1(*args, **kwargs):
        v1_called[0] += 1
        return orig_v1(*args, **kwargs)
    def spy_v2(*args, **kwargs):
        v2_called[0] += 1
        return orig_v2(*args, **kwargs)

    monkeypatch.setattr(worker_mod, "_detect_contradictions", spy_v1)
    monkeypatch.setattr(worker_mod, "_detect_contradictions_neighborhood", spy_v2)
    monkeypatch.setenv("DREAM_CONTRADICTION_MAX_CALLS", "1")  # ensure pass is exercised

    store = _seed(_FIXED_NOW, ("a", "x", 1), ("b", "y", 1))
    worker_mod.DreamingWorker(store).run()

    assert v1_called[0] == 1, "v1 contradiction path should be called once"
    assert v2_called[0] == 0, "v2 path must NOT be called when flag is off"


def test_dream_routes_to_v2_path_when_flag_on(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-028 §2 PR #2d — when `DREAM_CONTRADICTION_NEIGHBORHOOD=1`, `dream()`
    calls `_detect_contradictions_neighborhood` (v2) NOT the v1 path."""
    from memeval.dreaming import worker as worker_mod

    monkeypatch.setenv("DREAM_CONTRADICTION_NEIGHBORHOOD", "1")
    monkeypatch.setenv("DREAM_CONTRADICTION_MAX_CALLS", "1")

    v1_called = [0]
    v2_called = [0]
    orig_v1 = worker_mod._detect_contradictions
    orig_v2 = worker_mod._detect_contradictions_neighborhood

    def spy_v1(*args, **kwargs):
        v1_called[0] += 1
        return orig_v1(*args, **kwargs)
    def spy_v2(*args, **kwargs):
        v2_called[0] += 1
        return orig_v2(*args, **kwargs)

    monkeypatch.setattr(worker_mod, "_detect_contradictions", spy_v1)
    monkeypatch.setattr(worker_mod, "_detect_contradictions_neighborhood", spy_v2)

    store = _seed(_FIXED_NOW, ("a", "x", 1), ("b", "y", 1))
    worker_mod.DreamingWorker(store).run()

    assert v2_called[0] == 1, "v2 neighborhood path should be called once"
    assert v1_called[0] == 0, "v1 path must NOT be called when flag is on"


# --------------------------------------------------------------------------- #
# §P — ADR-dreaming-028 §2 PR #2e neighborhood-scoped dedup helper
# --------------------------------------------------------------------------- #

def test_dedup_neighborhood_returns_empty_for_empty_items(memory_store_dir: Path) -> None:
    """ADR-028 §2 PR #2e — empty items short-circuits with empty result, no LLM."""
    from memeval.dreaming.worker import _detect_duplicates_neighborhood

    llm = _StubLLM(responses=[])
    result = _detect_duplicates_neighborhood(
        items=[], store=_DeleteAwareStore(), client=llm,
        max_calls=10, model="stub", session_id="s1", now=_FIXED_NOW,
    )
    assert result.pairs == []
    assert result.llm_calls == 0
    assert llm.calls == []


def test_dedup_neighborhood_respects_max_calls_zero(memory_store_dir: Path) -> None:
    """ADR-028 §2 PR #2e — max_calls=0 returns empty result, no LLM call."""
    from memeval.dreaming.worker import _detect_duplicates_neighborhood

    pivot = MemoryItem(item_id="p", content="x", timestamp=_FIXED_NOW)
    llm = _StubLLM(responses=[])
    result = _detect_duplicates_neighborhood(
        items=[pivot], store=_DeleteAwareStore(), client=llm,
        max_calls=0, model="stub", session_id="s1", now=_FIXED_NOW,
    )
    assert result.pairs == []
    assert llm.calls == []


def test_dedup_neighborhood_skips_pivots_with_empty_neighborhood(memory_store_dir: Path) -> None:
    """ADR-028 §2 PR #2e — thin neighborhood (no neighbors) → skip without
    burning an LLM call."""
    from memeval.dreaming.worker import _detect_duplicates_neighborhood

    p1 = MemoryItem(item_id="p1", content="a", timestamp=_FIXED_NOW)
    p2 = MemoryItem(item_id="p2", content="b", timestamp=_FIXED_NOW)
    n1 = MemoryItem(item_id="n1", content="c", timestamp=_FIXED_NOW)
    store = _StubSearchStore(lookup={"b": [n1]})

    llm = _StubLLM(responses=[_StubCompletion('{"pairs": []}')])
    result = _detect_duplicates_neighborhood(
        items=[p1, p2], store=store, client=llm,
        max_calls=10, model="stub", session_id="s1", now=_FIXED_NOW,
    )
    assert result.llm_calls == 1, "only p2 had a neighborhood; p1 should skip"


def test_dedup_neighborhood_finds_pair_within_neighborhood(memory_store_dir: Path) -> None:
    """ADR-028 §2 PR #2e — LLM identifies a same-thing pair within the
    pivot's neighborhood; helper returns it as a DedupPair after
    deterministic loser selection (latest timestamp wins)."""
    from memeval.dreaming.worker import DedupPair, _detect_duplicates_neighborhood

    pivot = MemoryItem(item_id="p", content="paginator needs __iter__", timestamp=_FIXED_NOW)
    nbr = MemoryItem(item_id="n", content="added iteration to paginator", timestamp=_FIXED_NOW - _DAY)
    store = _StubSearchStore(lookup={"paginator needs __iter__": [nbr]})

    resp = _StubCompletion('{"pairs": [{"a_id": "p", "b_id": "n", "rationale": "same fix"}]}')
    llm = _StubLLM(responses=[resp])
    result = _detect_duplicates_neighborhood(
        items=[pivot], store=store, client=llm,
        max_calls=10, model="stub", session_id="s1", now=_FIXED_NOW,
    )
    assert len(result.pairs) == 1
    pair = result.pairs[0]
    assert isinstance(pair, DedupPair)
    # p is newer (_FIXED_NOW) → winner; n is older → loser.
    assert pair.winner_id == "p"
    assert pair.loser_id == "n"
    assert pair.rationale == "same fix"


def test_dedup_neighborhood_dedupes_pair_surfaced_from_two_pivots(memory_store_dir: Path) -> None:
    """ADR-028 §2 PR #2e — cross-pivot dedup: same pair surfaced from both A's
    neighborhood and B's neighborhood is returned ONCE, not twice."""
    from memeval.dreaming.worker import _detect_duplicates_neighborhood

    a = MemoryItem(item_id="A", content="a-content", timestamp=_FIXED_NOW)
    b = MemoryItem(item_id="B", content="b-content", timestamp=_FIXED_NOW - _DAY)
    store = _StubSearchStore(lookup={"a-content": [b], "b-content": [a]})

    resp_a = _StubCompletion('{"pairs": [{"a_id": "A", "b_id": "B", "rationale": "same"}]}')
    resp_b = _StubCompletion('{"pairs": [{"a_id": "B", "b_id": "A", "rationale": "still same"}]}')
    llm = _StubLLM(responses=[resp_a, resp_b])
    result = _detect_duplicates_neighborhood(
        items=[a, b], store=store, client=llm,
        max_calls=10, model="stub", session_id="s1", now=_FIXED_NOW,
    )
    assert len(result.pairs) == 1, "same pair must not be returned twice"
    assert result.llm_calls == 2, "both pivots judged; dedup happens after the LLM call"


def test_dedup_neighborhood_bounds_pivots_by_max_calls(memory_store_dir: Path) -> None:
    """ADR-028 §2 PR #2e — max_calls caps PIVOT count."""
    from memeval.dreaming.worker import _detect_duplicates_neighborhood

    items = [
        MemoryItem(item_id=f"p{i}", content=f"c{i}", timestamp=_FIXED_NOW)
        for i in range(5)
    ]
    nbr = MemoryItem(item_id="nbr", content="ny", timestamp=_FIXED_NOW)
    store = _StubSearchStore(lookup={p.content: [nbr] for p in items})

    llm = _StubLLM(responses=[_StubCompletion('{"pairs": []}') for _ in range(10)])
    result = _detect_duplicates_neighborhood(
        items=items, store=store, client=llm,
        max_calls=3, model="stub", session_id="s1", now=_FIXED_NOW,
    )
    assert len(llm.calls) == 3
    assert result.llm_calls == 3


def test_dedup_neighborhood_drops_pair_with_protected_loser(memory_store_dir: Path) -> None:
    """ADR-028 §2 PR #2e — pair whose loser_id is in protected_ids is dropped.
    Conservative posture parallel to the contradiction helper."""
    from memeval.dreaming.worker import _detect_duplicates_neighborhood

    p = MemoryItem(item_id="p", content="x", timestamp=_FIXED_NOW)
    n = MemoryItem(item_id="n", content="y", timestamp=_FIXED_NOW - _DAY)
    store = _StubSearchStore(lookup={"x": [n]})

    llm = _StubLLM(responses=[_StubCompletion('{"pairs": [{"a_id": "p", "b_id": "n", "rationale": "r"}]}')])
    result = _detect_duplicates_neighborhood(
        items=[p], store=store, client=llm,
        max_calls=10, model="stub", session_id="s1", now=_FIXED_NOW,
        protected_ids={"n"},
    )
    assert result.pairs == [], "pair with protected loser must be dropped"


def test_dedup_neighborhood_fails_open_on_llm_exception(memory_store_dir: Path) -> None:
    """ADR-028 §2 PR #2e + ADR-harness-006 — LLM raising MUST NOT crash.
    Affected pivot is skipped via `dream.dedup_skipped_unavailable_llm`."""
    from memeval.dreaming.worker import _detect_duplicates_neighborhood

    p1 = MemoryItem(item_id="p1", content="x", timestamp=_FIXED_NOW)
    p2 = MemoryItem(item_id="p2", content="y", timestamp=_FIXED_NOW)
    n = MemoryItem(item_id="nbr", content="z", timestamp=_FIXED_NOW)
    store = _StubSearchStore(lookup={"x": [n], "y": [n]})

    class _RaisingLLM(_StubLLM):
        def complete(self, *args, **kwargs):
            self.calls.append({})
            raise RuntimeError("Voyage API error (HTTP 429)")

    llm = _RaisingLLM(responses=[])
    result = _detect_duplicates_neighborhood(
        items=[p1, p2], store=store, client=llm,
        max_calls=10, model="stub", session_id="s1", now=_FIXED_NOW,
    )
    assert result.pairs == []
    assert result.llm_calls == 2, "fail-open still burns a call (Pushback H parity)"


# --------------------------------------------------------------------------- #
# §Q — ADR-dreaming-028 §2 PR #2f dedup-neighborhood feature-flag wiring
# --------------------------------------------------------------------------- #

def test_use_neighborhood_dedup_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-028 §2 PR #2f — default unset → False (lexical-dedup-only)."""
    from memeval.dreaming.worker import _read_use_neighborhood_dedup
    monkeypatch.delenv("DREAM_DEDUP_NEIGHBORHOOD", raising=False)
    assert _read_use_neighborhood_dedup() is False


def test_use_neighborhood_dedup_flag_one_enables(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-028 §2 PR #2f — exactly `"1"` enables the dedup pass."""
    from memeval.dreaming.worker import _read_use_neighborhood_dedup
    monkeypatch.setenv("DREAM_DEDUP_NEIGHBORHOOD", "1")
    assert _read_use_neighborhood_dedup() is True


def test_use_neighborhood_dedup_other_values_read_as_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-028 §2 PR #2f — strict equality with `"1"` only (parallel to the
    contradiction flag). Wrong spellings read as off — operator typos don't
    silently flip dedup behavior."""
    from memeval.dreaming.worker import _read_use_neighborhood_dedup
    for val in ("0", "", "true", "True", "yes", "on", "TRUE", " 1 ", "1 "):
        monkeypatch.setenv("DREAM_DEDUP_NEIGHBORHOOD", val)
        assert _read_use_neighborhood_dedup() is False, (
            f"DREAM_DEDUP_NEIGHBORHOOD={val!r} must read as off"
        )


def test_dream_skips_dedup_neighborhood_when_flag_off(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-028 §2 PR #2f — when flag is off (default), `dream()` does NOT
    invoke `_detect_duplicates_neighborhood`."""
    from memeval.dreaming import worker as worker_mod

    monkeypatch.delenv("DREAM_DEDUP_NEIGHBORHOOD", raising=False)

    dedup_called = [0]
    orig = worker_mod._detect_duplicates_neighborhood

    def spy(*args, **kwargs):
        dedup_called[0] += 1
        return orig(*args, **kwargs)

    monkeypatch.setattr(worker_mod, "_detect_duplicates_neighborhood", spy)

    store = _seed(_FIXED_NOW, ("a", "x", 1), ("b", "y", 1))
    worker_mod.DreamingWorker(store).run()

    assert dedup_called[0] == 0, (
        "_detect_duplicates_neighborhood must NOT be called when flag is off"
    )


def test_dream_invokes_dedup_neighborhood_when_flag_on(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-028 §2 PR #2f — `DREAM_DEDUP_NEIGHBORHOOD=1` causes `dream()` to
    invoke the LLM dedup pre-pass exactly once per run."""
    from memeval.dreaming import worker as worker_mod
    from memeval.dreaming.worker import DedupResult

    monkeypatch.setenv("DREAM_DEDUP_NEIGHBORHOOD", "1")
    monkeypatch.setenv("DREAM_CONTRADICTION_MAX_CALLS", "1")

    dedup_called = [0]

    def stub_dedup(*args, **kwargs):
        dedup_called[0] += 1
        # Return empty result so the dream flow continues cleanly.
        return DedupResult(
            pairs=[], llm_calls=0, tokens_in=0, tokens_out=0,
            cost_usd=0.0, pairs_examined_estimate=0,
        )

    monkeypatch.setattr(worker_mod, "_detect_duplicates_neighborhood", stub_dedup)
    # Fake LLM client. Returns an empty-pairs JSON so the contradiction
    # path (which still runs after dedup) doesn't crash on a None response.
    monkeypatch.setattr(
        worker_mod, "_make_llm_client",
        lambda: type("_FakeClient", (), {
            "model": "fake",
            "complete": lambda self, *a, **k: _StubCompletion('{"pairs": []}'),
        })(),
    )

    store = _seed(_FIXED_NOW, ("a", "x", 1), ("b", "y", 1))
    worker_mod.DreamingWorker(store).run()

    assert dedup_called[0] == 1, (
        f"_detect_duplicates_neighborhood expected once; got {dedup_called[0]}"
    )


def test_dream_dedup_neighborhood_losers_excluded_from_contradiction_pass(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-028 §2 PR #2f — items the LLM dedup pass retired are added to
    `retired_ids_set` so the contradiction pass's working set EXCLUDES them.
    Prevents re-judging an already-deleted item as a contradiction candidate.
    """
    from memeval.dreaming import worker as worker_mod
    from memeval.dreaming.worker import DedupPair, DedupResult

    monkeypatch.setenv("DREAM_DEDUP_NEIGHBORHOOD", "1")
    monkeypatch.setenv("DREAM_CONTRADICTION_MAX_CALLS", "1")

    contradiction_seen_ids: list[set[str]] = []

    def stub_dedup(*args, **kwargs):
        # Pretend the LLM found one duplicate pair: "b" loses to "a".
        return DedupResult(
            pairs=[DedupPair(loser_id="b", winner_id="a", rationale="dup")],
            llm_calls=1, tokens_in=10, tokens_out=10, cost_usd=0.0,
            pairs_examined_estimate=1,
        )

    def stub_contradiction(items, client, **kwargs):
        contradiction_seen_ids.append({it.item_id for it in items})
        from memeval.dreaming.worker import ContradictionResult
        return ContradictionResult(
            pairs=[], llm_calls=0, tokens_in=0, tokens_out=0,
            cost_usd=0.0, pairs_examined_estimate=0,
        )

    monkeypatch.setattr(worker_mod, "_detect_duplicates_neighborhood", stub_dedup)
    monkeypatch.setattr(worker_mod, "_detect_contradictions", stub_contradiction)
    monkeypatch.setattr(
        worker_mod, "_make_llm_client",
        lambda: type("_FakeClient", (), {
            "model": "fake",
            "complete": lambda self, *a, **k: _StubCompletion('{"pairs": []}'),
        })(),
    )

    store = _seed(_FIXED_NOW, ("a", "x", 1), ("b", "y", 1))
    worker_mod.DreamingWorker(store).run()

    assert contradiction_seen_ids, "contradiction pass should run after dedup"
    assert "b" not in contradiction_seen_ids[0], (
        "b was retired by dedup-neighborhood; it MUST NOT appear in the "
        "contradiction working set"
    )
    assert "a" in contradiction_seen_ids[0]
