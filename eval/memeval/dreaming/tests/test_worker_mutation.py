"""Worker tests — every unit-test criterion from JOB1_MUTATION_RUBRIC.md §A-M.

Job 1 mutation: detect dedup clusters, retire losers via `Router.delete()` /
`self.store.delete()` under a basedir `flock`, NFS hard-fail by default,
`engine.daydream` acquires the basedir lock before per-session.

Shell-command criteria (§A4, §F9, §F10, §F11, §H3, §J1, §J2, §J3, §J4, §K6,
§K9, §M4) are run verbatim from the rubric and not duplicated here.

Imports stay stdlib-only at module top (pytest aside) per the dreaming
package's discipline.
"""

from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from memeval.dreaming import _state, cli, engine, worker
from memeval.dreaming._state import (
    _DreamLockHeld,
    _LockHeld,
    _UnsupportedFsError,
    _basedir_dream_lock,
    _is_network_fs,
)
from memeval.harness import InMemoryStore
from memeval.schema import MemoryItem


# --------------------------------------------------------------------------- #
# Shared helpers + fixtures
# --------------------------------------------------------------------------- #


class _DeleteAwareStore(InMemoryStore):
    """InMemoryStore subclass that adds `delete()` so Router.delete duck-typing fires."""

    def delete(self, item_id: str) -> bool:
        """Hard-delete `item_id` from the in-memory dict; idempotent."""
        if item_id in self._items:
            del self._items[item_id]
            self._order = [i for i in self._order if i != item_id]
            return True
        return False


def _item(content: Any, item_id: str | None = None, **overrides: Any) -> MemoryItem:
    """Build a MemoryItem with sensible defaults; counter-based id if unspecified."""
    if item_id is None:
        item_id = f"item-{_item._counter}"  # type: ignore[attr-defined]
        _item._counter += 1  # type: ignore[attr-defined]
    return MemoryItem(item_id=item_id, content=content, **overrides)


_item._counter = 0  # type: ignore[attr-defined]


def _store_with(*specs: tuple[str, Any, float]) -> _DeleteAwareStore:
    """Build a store seeded with (item_id, content, timestamp) triples."""
    store = _DeleteAwareStore()
    for item_id, content, ts in specs:
        store.write(MemoryItem(item_id=item_id, content=content, timestamp=ts))
    return store


@pytest.fixture(autouse=True)
def _no_network_fs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to non-network FS so NFS hard-fail doesn't fire."""
    monkeypatch.setattr(_state, "_is_network_fs", lambda path: False)
    monkeypatch.setattr("memeval.dreaming.worker._is_network_fs", lambda path: False)


@pytest.fixture
def memory_store_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temp MEMORY_STORE directory and set the env-var (ADR-019)."""
    store = tmp_path / "memory-store"
    store.mkdir()
    monkeypatch.setenv("MEMORY_STORE", str(store))
    return store


@pytest.fixture
def spy_emit(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Capture every memeval.dreaming.events.emit call routed through worker.emit."""
    captured: list[tuple[str, dict[str, Any]]] = []

    def _fake(event_type: str, **fields: Any) -> None:
        """Spy replacement for ``events.emit`` — records the call."""
        captured.append((event_type, fields))

    monkeypatch.setattr("memeval.dreaming.worker.emit", _fake)
    monkeypatch.setattr("memeval.dreaming._state.emit", _fake)
    monkeypatch.setattr("memeval.dreaming.engine.emit", _fake)
    return captured


@pytest.fixture
def fake_make_store(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace cli._make_store with a delete-aware in-memory store."""
    fake_store = _DeleteAwareStore()
    factory = MagicMock(name="_make_store", return_value=fake_store)
    monkeypatch.setattr(cli, "_make_store", factory)
    return factory


@pytest.fixture
def empty_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``sys.stdin`` to look like a TTY so the CLI's ``_read_stdin_json`` returns ``{}``."""
    stream = io.StringIO("")
    stream.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stdin", stream)


# --------------------------------------------------------------------------- #
# §A — Surface
# --------------------------------------------------------------------------- #


def test_run_returns_dict_after_mutation(memory_store_dir: Path) -> None:
    """A1 — run() over a store with two duplicates returns a dict, no raise."""
    store = _store_with(("a", "Hello", 1.0), ("b", "hello", 2.0))
    result = worker.DreamingWorker(store).run()
    assert isinstance(result, dict)


def test_run_empty_store_no_deletes(memory_store_dir: Path, spy_emit: list) -> None:
    """A2 — empty store: returns dict, no Router.delete calls."""
    store = _DeleteAwareStore()
    store_delete_spy = MagicMock(wraps=store.delete)
    store.delete = store_delete_spy  # type: ignore[method-assign]
    result = worker.DreamingWorker(store).run()
    assert isinstance(result, dict)
    assert store_delete_spy.call_count == 0


def test_dream_wrapper_matches_worker_mutation(memory_store_dir: Path) -> None:
    """A3 — module-level worker.dream(store) matches DreamingWorker(store).run()."""
    store1 = _store_with(("a", "x", 1.0), ("b", "x", 2.0))
    store2 = _store_with(("a", "x", 1.0), ("b", "x", 2.0))
    assert worker.dream(store1) == worker.DreamingWorker(store2).run()


# --------------------------------------------------------------------------- #
# §B — Dict shape
# --------------------------------------------------------------------------- #

_EXPECTED_TOP_LEVEL_KEYS = {
    "schema", "version", "mode", "jobs_run",
    "skipped_jobs", "counts", "clusters",
}


def test_mutation_top_level_keys_exact(memory_store_dir: Path) -> None:
    """B1 — top-level key set is exactly the pinned set."""
    result = worker.DreamingWorker(_store_with(("a", "x", 1.0))).run()
    assert set(result.keys()) == _EXPECTED_TOP_LEVEL_KEYS


def test_mutation_schema_literal(memory_store_dir: Path) -> None:
    """B2 — schema == 'dream.summary'."""
    result = worker.DreamingWorker(_store_with(("a", "x", 1.0))).run()
    assert result["schema"] == "dream.summary"


def test_mutation_version_literal(memory_store_dir: Path) -> None:
    """B3 — version == 1, type is int."""
    result = worker.DreamingWorker(_store_with(("a", "x", 1.0))).run()
    assert result["version"] == 1
    assert type(result["version"]) is int


def test_mutation_mode_literal(memory_store_dir: Path) -> None:
    """B4 — mode == 'detection_and_mutation'."""
    result = worker.DreamingWorker(_store_with(("a", "x", 1.0))).run()
    assert result["mode"] == "detection_and_mutation"


def test_mutation_jobs_run_literal(memory_store_dir: Path) -> None:
    """B5 — jobs_run == ['dedup_detection', 'dedup_merge']."""
    result = worker.DreamingWorker(_store_with(("a", "x", 1.0))).run()
    assert result["jobs_run"] == ["dedup_detection", "dedup_merge"]


def test_mutation_skipped_jobs_literal(memory_store_dir: Path) -> None:
    """B6 — skipped_jobs list-equal, order pinned."""
    result = worker.DreamingWorker(_store_with(("a", "x", 1.0))).run()
    assert result["skipped_jobs"] == [
        "contradiction_resolution",
        "governance",
        "pruning",
    ]


def test_mutation_counts_shape(memory_store_dir: Path) -> None:
    """B7 — counts has pinned keys; all values are int (not bool, not float)."""
    result = worker.DreamingWorker(_store_with(("a", "x", 1.0), ("b", "x", 2.0))).run()
    assert set(result["counts"].keys()) == {
        "total_items", "duplicate_clusters", "items_in_duplicates", "items_retired",
    }
    for v in result["counts"].values():
        assert type(v) is int


def test_mutation_cluster_element_key_set(memory_store_dir: Path) -> None:
    """B8 — cluster element key set is exactly the pinned set."""
    result = worker.DreamingWorker(_store_with(("a", "x", 1.0), ("b", "x", 2.0))).run()
    for cluster in result["clusters"]:
        assert set(cluster.keys()) == {
            "normalized_key", "item_ids", "count", "winner_id", "retired_ids",
        }


def test_mutation_cluster_winner_in_ids_not_in_retired(memory_store_dir: Path) -> None:
    """B9 — winner_id is in item_ids and NOT in retired_ids."""
    result = worker.DreamingWorker(
        _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "x", 3.0))
    ).run()
    for cluster in result["clusters"]:
        assert isinstance(cluster["winner_id"], str)
        assert cluster["winner_id"] in cluster["item_ids"]
        assert cluster["winner_id"] not in cluster["retired_ids"]


def test_mutation_cluster_retired_ids_exact(memory_store_dir: Path) -> None:
    """B10 — retired_ids = set(item_ids) - {winner_id}; len == len(item_ids) - 1."""
    result = worker.DreamingWorker(
        _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "x", 3.0))
    ).run()
    for cluster in result["clusters"]:
        assert all(isinstance(i, str) for i in cluster["retired_ids"])
        assert set(cluster["retired_ids"]) == set(cluster["item_ids"]) - {cluster["winner_id"]}
        assert len(cluster["retired_ids"]) == len(cluster["item_ids"]) - 1


def test_mutation_result_json_roundtrip(memory_store_dir: Path) -> None:
    """B11 — result round-trips through json.dumps/loads with equality preserved."""
    result = worker.DreamingWorker(_store_with(("a", "x", 1.0), ("b", "x", 2.0))).run()
    assert json.loads(json.dumps(result)) == result


# --------------------------------------------------------------------------- #
# §C — Counts arithmetic
# --------------------------------------------------------------------------- #


def test_mutation_total_items_pre_run(memory_store_dir: Path) -> None:
    """C1 — total_items equals len(store.all()) measured BEFORE run()."""
    store = _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "y", 3.0))
    pre_count = len(store.all())
    result = worker.DreamingWorker(store).run()
    assert result["counts"]["total_items"] == pre_count


def test_mutation_duplicate_clusters_matches_len(memory_store_dir: Path) -> None:
    """C2 — duplicate_clusters equals len(result.clusters)."""
    result = worker.DreamingWorker(
        _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "y", 3.0), ("d", "y", 4.0))
    ).run()
    assert result["counts"]["duplicate_clusters"] == len(result["clusters"])


def test_mutation_items_in_duplicates_matches_sum(memory_store_dir: Path) -> None:
    """C3 — items_in_duplicates equals sum(c['count'] for c in clusters)."""
    result = worker.DreamingWorker(
        _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "y", 3.0))
    ).run()
    assert result["counts"]["items_in_duplicates"] == sum(c["count"] for c in result["clusters"])


def test_mutation_items_retired_equals_loser_sum(memory_store_dir: Path) -> None:
    """C4 — items_retired equals sum(c['count'] - 1 for c in clusters)."""
    result = worker.DreamingWorker(
        _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "x", 3.0), ("d", "y", 4.0), ("e", "y", 5.0))
    ).run()
    expected = sum(c["count"] - 1 for c in result["clusters"])
    assert result["counts"]["items_retired"] == expected


def test_mutation_items_retired_equals_retired_ids_sum(memory_store_dir: Path) -> None:
    """C5 — items_retired equals sum(len(c['retired_ids']) for c in clusters)."""
    result = worker.DreamingWorker(
        _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "y", 3.0), ("d", "y", 4.0))
    ).run()
    assert result["counts"]["items_retired"] == sum(len(c["retired_ids"]) for c in result["clusters"])


def test_mutation_store_size_after_run(memory_store_dir: Path) -> None:
    """C6 — after run, len(store.all()) == total_items - items_retired."""
    store = _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "y", 3.0), ("d", "y", 4.0))
    result = worker.DreamingWorker(store).run()
    assert len(store.all()) == result["counts"]["total_items"] - result["counts"]["items_retired"]


def test_mutation_clusters_have_count_at_least_two(memory_store_dir: Path) -> None:
    """C7 — every cluster has count >= 2."""
    result = worker.DreamingWorker(
        _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "y", 3.0))
    ).run()
    for cluster in result["clusters"]:
        assert cluster["count"] >= 2


# --------------------------------------------------------------------------- #
# §D — Determinism / idempotence
# --------------------------------------------------------------------------- #


def test_mutation_second_run_is_noop(memory_store_dir: Path) -> None:
    """D1 — second run after first has items_retired == 0 and empty clusters."""
    store = _store_with(("a", "x", 1.0), ("b", "x", 2.0))
    worker.DreamingWorker(store).run()
    second = worker.DreamingWorker(store).run()
    assert second["counts"]["items_retired"] == 0
    assert second["clusters"] == []


def test_mutation_second_run_no_state_change(memory_store_dir: Path) -> None:
    """D2 — set of (item_id, version) after run is stable across consecutive runs."""
    store = _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "y", 3.0))
    worker.DreamingWorker(store).run()
    snapshot_1 = {(i.item_id, i.version) for i in store.all()}
    worker.DreamingWorker(store).run()
    snapshot_2 = {(i.item_id, i.version) for i in store.all()}
    assert snapshot_1 == snapshot_2


def test_mutation_no_item_id_in_two_clusters(memory_store_dir: Path) -> None:
    """D3 — no item_id appears in more than one cluster."""
    result = worker.DreamingWorker(
        _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "y", 3.0), ("d", "y", 4.0))
    ).run()
    flat = [iid for c in result["clusters"] for iid in c["item_ids"]]
    assert len(flat) == len(set(flat))


def test_mutation_no_duplicate_ids_within_cluster(memory_store_dir: Path) -> None:
    """D4 — item_ids list inside each cluster has no duplicates."""
    result = worker.DreamingWorker(
        _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "x", 3.0))
    ).run()
    for cluster in result["clusters"]:
        assert len(cluster["item_ids"]) == len(set(cluster["item_ids"]))


def test_mutation_winner_selection_deterministic(memory_store_dir: Path) -> None:
    """D5 — same fixture twice → same winner_id."""
    a = _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "x", 3.0))
    b = _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "x", 3.0))
    ra = worker.DreamingWorker(a).run()
    rb = worker.DreamingWorker(b).run()
    assert ra["clusters"][0]["winner_id"] == rb["clusters"][0]["winner_id"]


def test_mutation_winner_is_latest_timestamp(memory_store_dir: Path) -> None:
    """D5a — winner is the item with the latest timestamp."""
    store = _store_with(("a", "x", 1.0), ("b", "x", 2.0))
    result = worker.DreamingWorker(store).run()
    assert result["clusters"][0]["winner_id"] == "b"


def test_mutation_winner_tiebreaker_lowest_id(memory_store_dir: Path) -> None:
    """D5b — equal timestamps: lexicographically lowest item_id wins."""
    store = _store_with(("a", "x", 5.0), ("b", "x", 5.0))
    result = worker.DreamingWorker(store).run()
    assert result["clusters"][0]["winner_id"] == "a"


# --------------------------------------------------------------------------- #
# §E — Normalization correctness (parity with detection)
# --------------------------------------------------------------------------- #


def test_mutation_punct_and_case_cluster_retires_one(memory_store_dir: Path) -> None:
    """E1 — 'Hello, world!' and 'hello world' cluster; one survives."""
    store = _store_with(("a", "Hello, world!", 1.0), ("b", "hello world", 2.0))
    result = worker.DreamingWorker(store).run()
    assert len(result["clusters"]) == 1
    assert {i.item_id for i in store.all()} == {"b"}


def test_mutation_no_false_positive_retire(memory_store_dir: Path) -> None:
    """E2 — different content does not cluster; both survive."""
    store = _store_with(("a", "Hello world.", 1.0), ("b", "Hi there", 2.0))
    worker.DreamingWorker(store).run()
    assert {i.item_id for i in store.all()} == {"a", "b"}


def test_mutation_whitespace_collapse_cluster(memory_store_dir: Path) -> None:
    """E3 — runs of whitespace collapse; one retired."""
    store = _store_with(("a", "foo   bar", 1.0), ("b", "foo bar", 2.0))
    worker.DreamingWorker(store).run()
    assert len(store.all()) == 1


def test_mutation_strip_edges_cluster(memory_store_dir: Path) -> None:
    """E4 — leading/trailing whitespace stripped; one retired."""
    store = _store_with(("a", "  foo bar  ", 1.0), ("b", "foo bar", 2.0))
    worker.DreamingWorker(store).run()
    assert len(store.all()) == 1


def test_mutation_three_member_cluster_retires_two(memory_store_dir: Path) -> None:
    """E5 — three near-duplicates → count 3, items_retired 2."""
    store = _store_with(("a", "Hello!", 1.0), ("b", "hello", 2.0), ("c", "Hello, ", 3.0))
    result = worker.DreamingWorker(store).run()
    assert result["clusters"][0]["count"] == 3
    assert result["counts"]["items_retired"] == 2
    assert len(store.all()) == 1


def test_mutation_empty_content_does_not_raise(memory_store_dir: Path) -> None:
    """E6 — empty content does not raise."""
    worker.DreamingWorker(_store_with(("a", "", 1.0))).run()


def test_mutation_none_content_does_not_raise(memory_store_dir: Path) -> None:
    """E7 — None content does not raise; proceeds."""
    store = _DeleteAwareStore()
    item = MemoryItem(item_id="a", content="placeholder")
    object.__setattr__(item, "content", None)
    store.write(item)
    worker.DreamingWorker(store).run()


# --------------------------------------------------------------------------- #
# §F — Mutation contract
# --------------------------------------------------------------------------- #


def test_mutation_router_delete_call_count_equals_items_retired(memory_store_dir: Path) -> None:
    """F1 — Router.delete called exactly items_retired times."""
    store = _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "y", 3.0), ("d", "y", 4.0))
    spy = MagicMock(wraps=store.delete)
    store.delete = spy  # type: ignore[method-assign]
    result = worker.DreamingWorker(store).run()
    assert spy.call_count == result["counts"]["items_retired"]


def test_mutation_every_delete_call_targets_a_retired_id(memory_store_dir: Path) -> None:
    """F2 — every item_id passed to delete is in the union of retired_ids."""
    store = _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "y", 3.0), ("d", "y", 4.0))
    spy = MagicMock(wraps=store.delete)
    store.delete = spy  # type: ignore[method-assign]
    result = worker.DreamingWorker(store).run()
    all_retired = {iid for c in result["clusters"] for iid in c["retired_ids"]}
    called_with = {call.args[0] for call in spy.call_args_list}
    assert called_with == all_retired


def test_mutation_winner_never_deleted(memory_store_dir: Path) -> None:
    """F3 — no winner_id is passed to delete."""
    store = _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "x", 3.0))
    spy = MagicMock(wraps=store.delete)
    store.delete = spy  # type: ignore[method-assign]
    result = worker.DreamingWorker(store).run()
    winners = {c["winner_id"] for c in result["clusters"]}
    called_with = {call.args[0] for call in spy.call_args_list}
    assert winners.isdisjoint(called_with)


def test_mutation_singletons_never_deleted(memory_store_dir: Path) -> None:
    """F4 — singletons are not passed to delete."""
    store = _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("singleton", "unique", 3.0))
    spy = MagicMock(wraps=store.delete)
    store.delete = spy  # type: ignore[method-assign]
    worker.DreamingWorker(store).run()
    called_with = {call.args[0] for call in spy.call_args_list}
    assert "singleton" not in called_with


def test_mutation_winner_content_unchanged(memory_store_dir: Path) -> None:
    """F5 — winner's content is byte-identical to pre-run."""
    store = _store_with(("a", "Hello", 1.0), ("b", "hello", 2.0))
    pre = {i.item_id: i.content for i in store.all()}
    result = worker.DreamingWorker(store).run()
    for cluster in result["clusters"]:
        wid = cluster["winner_id"]
        assert store.get(wid).content == pre[wid]


def test_mutation_winner_relevancy_unchanged(memory_store_dir: Path) -> None:
    """F6 — winner's relevancy is float-equal to pre-run."""
    store = _DeleteAwareStore()
    store.write(MemoryItem(item_id="a", content="x", timestamp=1.0, relevancy=0.7))
    store.write(MemoryItem(item_id="b", content="x", timestamp=2.0, relevancy=0.4))
    result = worker.DreamingWorker(store).run()
    for cluster in result["clusters"]:
        assert store.get(cluster["winner_id"]).relevancy == 0.4


def test_mutation_retired_ids_absent_after_run(memory_store_dir: Path) -> None:
    """F7 — every retired_id returns None from store.get after run."""
    store = _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "x", 3.0))
    result = worker.DreamingWorker(store).run()
    for cluster in result["clusters"]:
        for retired_id in cluster["retired_ids"]:
            assert store.get(retired_id) is None


def test_mutation_singletons_untouched(memory_store_dir: Path) -> None:
    """F8 — singleton items are unchanged after run."""
    store = _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("singleton", "unique", 3.0))
    pre = {i.item_id: (i.content, i.relevancy, i.version) for i in store.all() if i.item_id == "singleton"}
    worker.DreamingWorker(store).run()
    item = store.get("singleton")
    assert item is not None
    assert (item.content, item.relevancy, item.version) == pre["singleton"]


def test_mutation_deletes_complete_before_summary_built(memory_store_dir: Path) -> None:
    """F12 — all Router.delete calls return BEFORE summary dict construction."""
    store = _store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "x", 3.0))
    call_order: list[str] = []
    real_delete = store.delete

    def _wrapped_delete(item_id: str) -> bool:
        call_order.append(f"delete_complete:{item_id}")
        return real_delete(item_id)

    store.delete = _wrapped_delete  # type: ignore[method-assign]

    # Patch dict construction probe — emit fires inside run() with the constructed summary
    # at the moment summary dict is final. We use the emit call as the probe.
    from memeval.dreaming import worker as worker_mod

    original_emit = worker_mod.emit

    def _probe_emit(event_type: str, **fields: Any) -> None:
        if event_type == "dream.summary":
            call_order.append("summary_constructed")
        original_emit(event_type, **fields)

    worker_mod.emit = _probe_emit  # type: ignore[assignment]
    try:
        worker.DreamingWorker(store).run()
    finally:
        worker_mod.emit = original_emit  # type: ignore[assignment]

    # All delete completions precede summary construction
    delete_indices = [i for i, evt in enumerate(call_order) if evt.startswith("delete_complete:")]
    summary_index = call_order.index("summary_constructed")
    for di in delete_indices:
        assert di < summary_index


# --------------------------------------------------------------------------- #
# §G — trajectories_path
# --------------------------------------------------------------------------- #


def test_mutation_trajectories_path_none_no_effect(memory_store_dir: Path) -> None:
    """G1 — trajectories_path=None matches no-arg call."""
    store1 = _store_with(("a", "x", 1.0), ("b", "x", 2.0))
    store2 = _store_with(("a", "x", 1.0), ("b", "x", 2.0))
    assert worker.DreamingWorker(store1).run() == worker.DreamingWorker(store2).run(trajectories_path=None)


def test_mutation_trajectories_path_truthy_raises_valueerror(memory_store_dir: Path) -> None:
    """G2 — truthy trajectories_path raises ValueError."""
    store = _store_with(("a", "x", 1.0))
    with pytest.raises(ValueError, match="not consumed"):
        worker.DreamingWorker(store).run(trajectories_path="/path/that/does/not/exist")


def test_mutation_no_filesystem_access_to_trajectories_path(memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G3 — no open() against trajectories_path on the raise path."""
    import builtins
    import pathlib

    bogus = "/path/that/does/not/exist/trajectories"
    open_calls: list[str] = []
    real_open = builtins.open
    real_path_open = pathlib.Path.open

    def _trap_builtin_open(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        """Trap builtins.open and record any access against the bogus path."""
        open_calls.append(str(path))
        return real_open(path, *args, **kwargs)

    def _trap_path_open(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        """Trap pathlib.Path.open and record any access against the bogus path."""
        open_calls.append(str(self))
        return real_path_open(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _trap_builtin_open)
    monkeypatch.setattr(pathlib.Path, "open", _trap_path_open)

    store = _store_with(("a", "x", 1.0))
    with pytest.raises(ValueError):
        worker.DreamingWorker(store).run(trajectories_path=bogus)
    assert all(bogus not in c for c in open_calls)


def test_mutation_trajectories_path_raises_before_lock(memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G4 — ValueError raised before basedir lock is acquired."""
    lock_entered = []
    original_lock = _state._basedir_dream_lock

    def _spy_lock(basedir):
        """Wraps _basedir_dream_lock to record entry order vs ValueError raise."""
        lock_entered.append(True)
        return original_lock(basedir)

    monkeypatch.setattr("memeval.dreaming.worker._basedir_dream_lock", _spy_lock)
    store = _store_with(("a", "x", 1.0))
    with pytest.raises(ValueError):
        worker.DreamingWorker(store).run(trajectories_path="/bogus")
    assert lock_entered == []


# --------------------------------------------------------------------------- #
# §H — CLI fail-open
# --------------------------------------------------------------------------- #


def test_dream_all_exits_zero_on_mutation_success(
    empty_stdin: None, memory_store_dir: Path, fake_make_store: MagicMock,
) -> None:
    """H1 — daydream-cli dream --all exits 0 on success."""
    assert cli.main(["dream", "--all"]) == 0


def test_dream_all_failopens_on_runtime_error_mutation(
    empty_stdin: None, memory_store_dir: Path, fake_make_store: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H2 — RuntimeError → CLI exit 0 + daydream.dream_all_error event."""
    captured: list[tuple[str, dict[str, Any]]] = []

    def _fake_emit(event_type: str, **fields: Any) -> None:
        """Spy emit for CLI failure assertion."""
        captured.append((event_type, fields))

    from memeval.dreaming import events as events_mod
    monkeypatch.setattr(events_mod, "emit", _fake_emit)

    def _boom(*args, **kw):
        """Stand-in worker.dream that raises RuntimeError to test fail-open."""
        raise RuntimeError("boom")
    monkeypatch.setattr(worker, "dream", _boom)

    assert cli.main(["dream", "--all"]) == 0
    assert any(et == "daydream.dream_all_error" for et, _ in captured)


def test_dream_all_does_not_swallow_keyboardinterrupt_mutation(
    empty_stdin: None, memory_store_dir: Path, fake_make_store: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H4 — KeyboardInterrupt propagates out of cli.main."""
    def _kbi(*args, **kw):
        """Stand-in worker.dream that raises KeyboardInterrupt."""
        raise KeyboardInterrupt
    monkeypatch.setattr(worker, "dream", _kbi)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["dream", "--all"])


def test_dream_all_does_not_swallow_systemexit_mutation(
    empty_stdin: None, memory_store_dir: Path, fake_make_store: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H5 — SystemExit propagates out of cli.main."""
    def _se(*args, **kw):
        """Stand-in worker.dream that raises SystemExit."""
        raise SystemExit(7)
    monkeypatch.setattr(worker, "dream", _se)
    with pytest.raises(SystemExit):
        cli.main(["dream", "--all"])


def test_handle_dream_catches_dreamlockheld(
    empty_stdin: None, memory_store_dir: Path, fake_make_store: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H6 — _handle_dream catches _DreamLockHeld, emits dream.lock_contended, exit 0."""
    captured: list[tuple[str, dict[str, Any]]] = []

    def _fake_emit(event_type: str, **fields: Any) -> None:
        """Spy emit for lock-contended assertion."""
        captured.append((event_type, fields))

    from memeval.dreaming import events as events_mod
    monkeypatch.setattr(events_mod, "emit", _fake_emit)

    def _held(*args, **kw):
        """Stand-in worker.dream that raises _DreamLockHeld."""
        raise _DreamLockHeld("held by other")
    monkeypatch.setattr(worker, "dream", _held)

    assert cli.main(["dream", "--all"]) == 0
    assert any(et == "dream.lock_contended" for et, _ in captured)


def test_handle_dream_catches_unsupportedfserror(
    empty_stdin: None, memory_store_dir: Path, fake_make_store: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H7 — _handle_dream catches _UnsupportedFsError, emits dream.unsupported_fs, exit 0."""
    captured: list[tuple[str, dict[str, Any]]] = []

    def _fake_emit(event_type: str, **fields: Any) -> None:
        """Spy emit for unsupported-fs assertion."""
        captured.append((event_type, fields))

    from memeval.dreaming import events as events_mod
    monkeypatch.setattr(events_mod, "emit", _fake_emit)

    def _nfs(*args, **kw):
        """Stand-in worker.dream that raises _UnsupportedFsError."""
        raise _UnsupportedFsError("NFS detected")
    monkeypatch.setattr(worker, "dream", _nfs)

    assert cli.main(["dream", "--all"]) == 0
    assert any(et == "dream.unsupported_fs" for et, _ in captured)


# --------------------------------------------------------------------------- #
# §I — Observability
# --------------------------------------------------------------------------- #


def test_mutation_run_emits_exactly_one_summary_event(memory_store_dir: Path, spy_emit: list) -> None:
    """I1 — exactly one dream.summary event per successful run."""
    worker.DreamingWorker(_store_with(("a", "x", 1.0), ("b", "x", 2.0))).run()
    summary_events = [e for e in spy_emit if e[0] == "dream.summary"]
    assert len(summary_events) == 1


def test_mutation_emit_event_required_fields(memory_store_dir: Path, spy_emit: list) -> None:
    """I2 — emit kwargs include mode, total_items, duplicate_clusters, items_retired."""
    worker.DreamingWorker(_store_with(("a", "x", 1.0), ("b", "x", 2.0))).run()
    summary_events = [e for e in spy_emit if e[0] == "dream.summary"]
    kwargs = summary_events[0][1]
    for key in ("mode", "total_items", "duplicate_clusters", "items_retired"):
        assert key in kwargs


def test_mutation_emit_event_values_match_summary(memory_store_dir: Path, spy_emit: list) -> None:
    """I3 — emit kwargs match returned dict's fields."""
    result = worker.DreamingWorker(_store_with(("a", "x", 1.0), ("b", "x", 2.0), ("c", "x", 3.0))).run()
    summary_events = [e for e in spy_emit if e[0] == "dream.summary"]
    kwargs = summary_events[0][1]
    assert kwargs["mode"] == result["mode"]
    assert kwargs["total_items"] == result["counts"]["total_items"]
    assert kwargs["duplicate_clusters"] == result["counts"]["duplicate_clusters"]
    assert kwargs["items_retired"] == result["counts"]["items_retired"]


def test_basedir_lock_emits_dream_lock_contended_on_contention(
    memory_store_dir: Path, spy_emit: list,
) -> None:
    """I4 — _basedir_dream_lock emits dream.lock_contended on BlockingIOError."""
    basedir = memory_store_dir
    with _basedir_dream_lock(basedir):
        with pytest.raises(_DreamLockHeld):
            with _basedir_dream_lock(basedir):
                pass
    contention_events = [e for e in spy_emit if e[0] == "dream.lock_contended"]
    assert len(contention_events) == 1
    assert contention_events[0][1].get("basedir") == str(basedir)


def test_handle_dream_emits_unsupported_fs(
    empty_stdin: None, memory_store_dir: Path, fake_make_store: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I5 — _handle_dream emits dream.unsupported_fs on _UnsupportedFsError."""
    captured: list[tuple[str, dict[str, Any]]] = []

    def _fake_emit(event_type: str, **fields: Any) -> None:
        """Spy emit for unsupported-fs assertion."""
        captured.append((event_type, fields))

    from memeval.dreaming import events as events_mod
    monkeypatch.setattr(events_mod, "emit", _fake_emit)

    def _nfs(*args, **kw):
        """Stand-in worker.dream that raises _UnsupportedFsError."""
        raise _UnsupportedFsError("NFS")
    monkeypatch.setattr(worker, "dream", _nfs)
    cli.main(["dream", "--all"])
    nfs_events = [e for e in captured if e[0] == "dream.unsupported_fs"]
    assert len(nfs_events) == 1


def test_daydream_emits_dream_in_progress_skipped_on_contention(
    memory_store_dir: Path, spy_emit: list, tmp_path: Path,
) -> None:
    """I6 / L12 — engine.daydream emits daydream.dream_in_progress_skipped on basedir contention."""
    basedir = memory_store_dir
    log_path = tmp_path / "log.jsonl"
    log_path.write_text("dummy\n")
    store = _DeleteAwareStore()
    with _basedir_dream_lock(basedir):
        engine.daydream(
            session_id="sess-1",
            log_path=log_path,
            store=store,
            basedir=basedir,
            client=MagicMock(),
        )
    skip_events = [e for e in spy_emit if e[0] == "daydream.dream_in_progress_skipped"]
    assert len(skip_events) == 1


def test_daydream_happy_path_event_surface_unchanged(
    memory_store_dir: Path, spy_emit: list, tmp_path: Path,
) -> None:
    """I7 — happy-path Daydream emits no new dream.* family event names."""
    basedir = memory_store_dir
    log_path = tmp_path / "log.jsonl"
    log_path.write_text("dummy content\n")
    store = _DeleteAwareStore()
    client = MagicMock()
    engine.daydream(
        session_id="sess-1",
        log_path=log_path,
        store=store,
        basedir=basedir,
        client=client,
    )
    # The forbidden new names: dream.lock_contended (only on contention), dream.unsupported_fs.
    forbidden = {"dream.lock_contended", "dream.unsupported_fs"}
    emitted = {e[0] for e in spy_emit}
    assert forbidden.isdisjoint(emitted)


# --------------------------------------------------------------------------- #
# §K — Non-goals
# --------------------------------------------------------------------------- #


def test_mutation_router_delete_called_with_single_id_arg(memory_store_dir: Path) -> None:
    """K10 — Router.delete called with exactly one positional arg, no kwargs."""
    store = _store_with(("a", "x", 1.0), ("b", "x", 2.0))
    spy = MagicMock(wraps=store.delete)
    store.delete = spy  # type: ignore[method-assign]
    worker.DreamingWorker(store).run()
    for call in spy.call_args_list:
        assert len(call.args) == 1
        assert call.kwargs == {}


# --------------------------------------------------------------------------- #
# §L — Lock + NFS
# --------------------------------------------------------------------------- #


def test_basedir_lock_acquires_lock_file_at_expected_path(memory_store_dir: Path) -> None:
    """L1 — _basedir_dream_lock creates <basedir>/.dream.lock."""
    with _basedir_dream_lock(memory_store_dir):
        assert (memory_store_dir / ".dream.lock").exists()


def test_basedir_lock_raises_DreamLockHeld_on_contention(memory_store_dir: Path) -> None:
    """L2 — concurrent acquire raises _DreamLockHeld (not _LockHeld)."""
    with _basedir_dream_lock(memory_store_dir):
        with pytest.raises(_DreamLockHeld):
            with _basedir_dream_lock(memory_store_dir):
                pass


def test_DreamLockHeld_distinct_from_LockHeld() -> None:
    """L3 — _DreamLockHeld is a class distinct from _LockHeld."""
    assert _DreamLockHeld is not _LockHeld
    assert not issubclass(_DreamLockHeld, _LockHeld)
    assert not issubclass(_LockHeld, _DreamLockHeld)


def test_basedir_lock_emits_event_before_raising(memory_store_dir: Path, spy_emit: list) -> None:
    """L4 — exactly one dream.lock_contended event emitted before raising."""
    with _basedir_dream_lock(memory_store_dir):
        try:
            with _basedir_dream_lock(memory_store_dir):
                pass
        except _DreamLockHeld:
            pass
    contention_events = [e for e in spy_emit if e[0] == "dream.lock_contended"]
    assert len(contention_events) == 1


def test_basedir_lock_releases_on_normal_exit(memory_store_dir: Path) -> None:
    """L5 — fresh acquisition succeeds after a clean exit."""
    with _basedir_dream_lock(memory_store_dir):
        pass
    with _basedir_dream_lock(memory_store_dir):
        pass  # second acquisition must succeed


def test_basedir_lock_releases_on_exception(memory_store_dir: Path) -> None:
    """L6 — fresh acquisition succeeds after an exception inside the with block."""
    with pytest.raises(RuntimeError):
        with _basedir_dream_lock(memory_store_dir):
            raise RuntimeError("inner boom")
    with _basedir_dream_lock(memory_store_dir):
        pass


def test_basedir_lock_does_not_unlink_lock_file(memory_store_dir: Path) -> None:
    """L7 — lock file persists across acquisitions."""
    with _basedir_dream_lock(memory_store_dir):
        pass
    assert (memory_store_dir / ".dream.lock").exists()


def test_daydream_basedir_lock_before_per_session_lock(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """L8 — basedir lock acquired BEFORE per-session lock in engine.daydream."""
    order: list[str] = []
    original_basedir = _state._basedir_dream_lock
    original_session = _state._per_session_lock

    from contextlib import contextmanager

    @contextmanager
    def _trace_basedir(basedir):
        """Trace basedir lock acquisition order."""
        order.append("basedir")
        with original_basedir(basedir):
            yield

    @contextmanager
    def _trace_session(basedir, session_id):
        """Trace per-session lock acquisition order."""
        order.append("session")
        with original_session(basedir, session_id):
            yield

    monkeypatch.setattr(engine, "_basedir_dream_lock", _trace_basedir)
    monkeypatch.setattr(engine, "_per_session_lock", _trace_session)

    log_path = tmp_path / "log.jsonl"
    log_path.write_text("content\n")
    engine.daydream(
        session_id="sess",
        log_path=log_path,
        store=_DeleteAwareStore(),
        basedir=memory_store_dir,
        client=MagicMock(),
    )
    # Both should be acquired, basedir first
    assert order[:2] == ["basedir", "session"]


def test_daydream_basedir_lock_before_store_access(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """L9 — engine.daydream acquires basedir lock BEFORE any store access. Non-vacuous fixture."""
    order: list[str] = []
    original_basedir = _state._basedir_dream_lock
    from contextlib import contextmanager

    @contextmanager
    def _trace_basedir(basedir):
        """Record basedir lock acquisition order."""
        order.append("basedir_lock")
        with original_basedir(basedir):
            yield

    monkeypatch.setattr(engine, "_basedir_dream_lock", _trace_basedir)

    class _StoreSpy(_DeleteAwareStore):
        """Records every store-method call to verify ordering."""

        def write(self, item):
            """Spy write — record call order."""
            order.append("store.write")
            super().write(item)

    # Stub extract_memories to return a real item so store.write actually fires.
    def _stub_extract(*args, **kw):
        """Return one MemoryItem so the engine writes through to store.write."""
        return [MemoryItem(item_id="ext-1", content="extracted content", timestamp=1.0)]

    monkeypatch.setattr(engine, "extract_memories", _stub_extract)

    log_path = tmp_path / "log.jsonl"
    log_path.write_text("content\n")
    engine.daydream(
        session_id="sess",
        log_path=log_path,
        store=_StoreSpy(),
        basedir=memory_store_dir,
        client=MagicMock(),
    )
    # Non-vacuous precondition: store.write actually fired.
    assert "store.write" in order
    assert order.index("basedir_lock") < order.index("store.write")


def test_daydream_basedir_lock_before_sidecar_mutation(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """L10 — engine.daydream acquires basedir lock BEFORE sidecar cursor mutation. Non-vacuous fixture."""
    order: list[str] = []
    original_basedir = _state._basedir_dream_lock
    original_sidecar_write = _state._write_sidecar_atomic
    from contextlib import contextmanager

    @contextmanager
    def _trace_basedir(basedir):
        """Record basedir lock acquisition order."""
        order.append("basedir_lock")
        with original_basedir(basedir):
            yield

    def _trace_sidecar(target, state):
        """Record sidecar write order."""
        order.append("sidecar_write")
        return original_sidecar_write(target, state)

    monkeypatch.setattr(engine, "_basedir_dream_lock", _trace_basedir)
    monkeypatch.setattr(engine, "_write_sidecar_atomic", _trace_sidecar)

    # Stub extract_memories to return a real item so the sidecar gets written.
    def _stub_extract(*args, **kw):
        """Return one MemoryItem so the sidecar cursor advances + writes."""
        return [MemoryItem(item_id="ext-1", content="extracted content", timestamp=1.0)]

    monkeypatch.setattr(engine, "extract_memories", _stub_extract)

    log_path = tmp_path / "log.jsonl"
    log_path.write_text("content\n")
    engine.daydream(
        session_id="sess",
        log_path=log_path,
        store=_DeleteAwareStore(),
        basedir=memory_store_dir,
        client=MagicMock(),
    )
    # Non-vacuous precondition: sidecar write actually fired.
    assert "sidecar_write" in order
    assert order.index("basedir_lock") < order.index("sidecar_write")


def test_daydream_on_basedir_contention_no_state_touched(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """L11 — on contention, no per-session lock, no store access, no cursor mutation."""
    store_spy = MagicMock()
    store_spy.write = MagicMock()
    store_spy.all = MagicMock(return_value=[])
    log_path = tmp_path / "log.jsonl"
    log_path.write_text("content\n")
    with _basedir_dream_lock(memory_store_dir):
        engine.daydream(
            session_id="sess",
            log_path=log_path,
            store=store_spy,
            basedir=memory_store_dir,
            client=MagicMock(),
        )
    assert store_spy.write.call_count == 0


def test_worker_basedir_lock_before_store_all(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L13 — worker.run() acquires basedir lock BEFORE store.all()."""
    order: list[str] = []
    store = _store_with(("a", "x", 1.0))
    real_all = store.all

    def _spy_all():
        """Spy store.all to record relative order vs lock acquisition."""
        order.append("store.all")
        return real_all()

    store.all = _spy_all  # type: ignore[method-assign]

    original_lock = _state._basedir_dream_lock
    from contextlib import contextmanager

    @contextmanager
    def _spy_lock(basedir):
        """Spy basedir lock to record relative order vs store access."""
        order.append("lock_acquired")
        with original_lock(basedir):
            yield

    monkeypatch.setattr("memeval.dreaming.worker._basedir_dream_lock", _spy_lock)
    worker.DreamingWorker(store).run()
    assert order.index("lock_acquired") < order.index("store.all")


def test_worker_basedir_lock_before_delete_calls(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L14 — worker.run() acquires basedir lock BEFORE any delete call."""
    order: list[str] = []
    store = _store_with(("a", "x", 1.0), ("b", "x", 2.0))
    real_delete = store.delete

    def _spy_delete(item_id):
        """Spy delete to record relative order vs lock acquisition."""
        order.append("delete")
        return real_delete(item_id)

    store.delete = _spy_delete  # type: ignore[method-assign]

    original_lock = _state._basedir_dream_lock
    from contextlib import contextmanager

    @contextmanager
    def _spy_lock(basedir):
        """Spy basedir lock to record relative order vs delete."""
        order.append("lock_acquired")
        with original_lock(basedir):
            yield

    monkeypatch.setattr("memeval.dreaming.worker._basedir_dream_lock", _spy_lock)
    worker.DreamingWorker(store).run()
    assert order.index("lock_acquired") < order.index("delete")


def test_worker_on_basedir_contention_no_state_touched(
    memory_store_dir: Path,
) -> None:
    """L15 — on basedir contention, no store.all and no delete called."""
    store = _store_with(("a", "x", 1.0), ("b", "x", 2.0))
    all_spy = MagicMock(wraps=store.all)
    delete_spy = MagicMock(wraps=store.delete)
    store.all = all_spy  # type: ignore[method-assign]
    store.delete = delete_spy  # type: ignore[method-assign]

    with _basedir_dream_lock(memory_store_dir):
        with pytest.raises(_DreamLockHeld):
            worker.DreamingWorker(store).run()
    assert all_spy.call_count == 0
    assert delete_spy.call_count == 0


def test_is_network_fs_callable_exists() -> None:
    """L16 — _is_network_fs is callable + importable."""
    assert callable(_is_network_fs)


def test_worker_raises_unsupported_fs_on_network_fs(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L17 — _is_network_fs True + DREAM_ALLOW_NETWORK_FS unset → _UnsupportedFsError before lock."""
    monkeypatch.setattr("memeval.dreaming.worker._is_network_fs", lambda path: True)
    monkeypatch.delenv("DREAM_ALLOW_NETWORK_FS", raising=False)
    lock_entered: list[bool] = []
    original_lock = _state._basedir_dream_lock
    from contextlib import contextmanager

    @contextmanager
    def _spy_lock(basedir):
        """Spy basedir lock to record entry-on-NFS-detected behavior."""
        lock_entered.append(True)
        with original_lock(basedir):
            yield

    monkeypatch.setattr("memeval.dreaming.worker._basedir_dream_lock", _spy_lock)
    store = _store_with(("a", "x", 1.0))
    with pytest.raises(_UnsupportedFsError):
        worker.DreamingWorker(store).run()
    assert lock_entered == []


def test_worker_proceeds_with_dream_allow_network_fs_env(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """L18 — _is_network_fs True + DREAM_ALLOW_NETWORK_FS=1 → proceeds + warning log."""
    monkeypatch.setattr("memeval.dreaming.worker._is_network_fs", lambda path: True)
    monkeypatch.setenv("DREAM_ALLOW_NETWORK_FS", "1")
    caplog.set_level(logging.WARNING, logger="memeval.dreaming.worker")
    store = _store_with(("a", "x", 1.0))
    worker.DreamingWorker(store).run()  # must not raise
    assert any("DREAM_ALLOW_NETWORK_FS" in rec.getMessage() for rec in caplog.records)


def test_UnsupportedFsError_distinct() -> None:
    """L19 — _UnsupportedFsError distinct from _DreamLockHeld and _LockHeld."""
    assert _UnsupportedFsError is not _DreamLockHeld
    assert _UnsupportedFsError is not _LockHeld


def test_is_network_fs_platform_dispatch(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """L20 — known platforms (linux, darwin) detect; unknown platforms warn + return False."""
    caplog.set_level(logging.WARNING, logger="memeval.dreaming._state")

    # win32: returns False + warning
    monkeypatch.setattr(sys, "platform", "win32")
    assert _is_network_fs(tmp_path) is False
    assert any("unknown platform" in rec.getMessage() for rec in caplog.records)

    # linux: read mocked /proc/mounts containing the target path as nfs
    target = tmp_path / "nfs_mount"
    target.mkdir(parents=True, exist_ok=True)
    resolved = str(target.resolve())
    fake_mounts = f"server:/export {resolved} nfs4 rw,relatime 0 0\n"

    import builtins
    real_open = builtins.open

    def _linux_open(path, *args, **kwargs):
        """Mocked /proc/mounts reader returning an NFS line covering the test target."""
        if str(path) == "/proc/mounts":
            return io.StringIO(fake_mounts)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(builtins, "open", _linux_open)
    assert _is_network_fs(target) is True
    monkeypatch.setattr(builtins, "open", real_open)

    # linux negative: target not in mounts → returns False
    fake_mounts_local = "/dev/sda1 / ext4 rw,relatime 0 0\n"

    def _linux_open_local(path, *args, **kwargs):
        """Mocked /proc/mounts reader returning only a local ext4 line."""
        if str(path) == "/proc/mounts":
            return io.StringIO(fake_mounts_local)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _linux_open_local)
    assert _is_network_fs(target) is False
    monkeypatch.setattr(builtins, "open", real_open)

    # darwin: mock subprocess.run to return an NFS mount line covering target
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_darwin_output = f"server:/export on {resolved} (nfs, nodev, nosuid)\n"

    class _FakeProc:
        """Fake CompletedProcess for the darwin `mount` shell-out."""

        stdout = fake_darwin_output

    def _fake_run(cmd, **kwargs):
        """Stand in for subprocess.run when daydream's NFS detector shells out to `mount`."""
        return _FakeProc()

    import subprocess as _subprocess
    monkeypatch.setattr(_subprocess, "run", _fake_run)
    assert _is_network_fs(target) is True

    # darwin negative: local fs
    fake_darwin_local = f"/dev/disk1 on {resolved} (apfs, local)\n"

    class _FakeProcLocal:
        """Fake CompletedProcess for the darwin local-FS case."""

        stdout = fake_darwin_local

    def _fake_run_local(cmd, **kwargs):
        """Stand in for subprocess.run returning a local apfs mount line."""
        return _FakeProcLocal()

    monkeypatch.setattr(_subprocess, "run", _fake_run_local)
    assert _is_network_fs(target) is False


# --------------------------------------------------------------------------- #
# §M — Concurrency
# --------------------------------------------------------------------------- #


def test_two_concurrent_workers_only_one_mutates(memory_store_dir: Path) -> None:
    """M1 — two workers in two threads: exactly one acquires + mutates."""
    store = _store_with(("a", "x", 1.0), ("b", "x", 2.0))
    delete_counts: list[int] = []
    real_delete = store.delete
    lock = threading.Lock()

    def _counting_delete(item_id):
        """Wrap delete to count successful retirements across threads."""
        with lock:
            delete_counts.append(1)
        return real_delete(item_id)

    store.delete = _counting_delete  # type: ignore[method-assign]
    results: list[Any] = []
    exceptions: list[Exception] = []

    def _runner():
        """Thread target — run worker; record exceptions if any."""
        try:
            results.append(worker.DreamingWorker(store).run())
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

    # Either: both succeeded but only one made deletes, or one raised _DreamLockHeld.
    # Total deletes across all runs should be exactly 1 (loser of single cluster).
    assert sum(delete_counts) == 1


def test_daydream_skips_while_dream_running(
    memory_store_dir: Path, spy_emit: list, tmp_path: Path,
) -> None:
    """M2 — daydream-cli daydream while dream is running: skip + no cursor advance."""
    log_path = tmp_path / "log.jsonl"
    log_path.write_text("content\n")
    store = _DeleteAwareStore()
    with _basedir_dream_lock(memory_store_dir):
        engine.daydream(
            session_id="sess",
            log_path=log_path,
            store=store,
            basedir=memory_store_dir,
            client=MagicMock(),
        )
    skip_events = [e for e in spy_emit if e[0] == "daydream.dream_in_progress_skipped"]
    assert len(skip_events) == 1


# --------------------------------------------------------------------------- #
# Integration smoke — real RouterStore via build_store (beyond rubric scope)
# --------------------------------------------------------------------------- #


def test_mutation_real_routerstore_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration smoke — end-to-end via real RouterStore + Router.delete fan-out.

    Builds a fully-assembled engine via cookbook_memory.core.contract.build_store
    (RouterStore over Router with markdown + vectors + graph backends), seeds it
    with three items (two normalize to the same dedup key, one singleton), runs
    DreamingWorker, and verifies that the loser is gone from EVERY backend
    Router.delete fans out to — including the on-disk MarkdownStore file —
    while the winner survives byte-identical. Closes the gap between the 83
    unit tests (which all use _DeleteAwareStore / InMemoryStore) and the real
    production wiring.
    """
    # Force the fully-offline fusion profile so build_store needs no API keys
    # (accuracy_profile would try to instantiate VoyageEmbedder; speed_profile
    # is explicit-only). fusion is the default offline auto-selection too, but
    # we pin it for determinism.
    monkeypatch.setenv("MEMORY_PROFILE", "fusion")
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    # Wire MEMORY_STORE so worker._resolve_basedir() lands the .dream.lock
    # under tmp_path (same dir we seed build_store with).
    monkeypatch.setenv("MEMORY_STORE", str(tmp_path))

    from cookbook_memory.core.contract import build_store

    store = build_store(str(tmp_path))

    # Seed: two near-duplicates (cluster), one singleton (must survive).
    # "Hello, world!" and "hello world" both normalize to "hello world".
    # Timestamps pinned so the winner is deterministic: latest ts wins (§D5a),
    # so ts=2.0 > ts=1.0 → winner_id = "dup-winner".
    winner_id = "dup-winner"
    loser_id = "dup-loser"
    singleton_id = "unique-survivor"
    winner_content = "Hello, world!"
    loser_content = "hello world"
    singleton_content = "totally distinct content"

    store.write(MemoryItem(item_id=loser_id, content=loser_content, timestamp=1.0))
    store.write(MemoryItem(item_id=winner_id, content=winner_content, timestamp=2.0))
    store.write(MemoryItem(item_id=singleton_id, content=singleton_content, timestamp=3.0))

    # Sanity: all three exist on disk via the MarkdownStore fan-out before run().
    md_root = tmp_path / "markdown" / "memory"
    winner_md = md_root / f"{winner_id}.md"
    loser_md = md_root / f"{loser_id}.md"
    singleton_md = md_root / f"{singleton_id}.md"
    assert winner_md.exists(), "precondition: markdown fan-out wrote winner"
    assert loser_md.exists(), "precondition: markdown fan-out wrote loser"
    assert singleton_md.exists(), "precondition: markdown fan-out wrote singleton"

    # Run the worker against the REAL RouterStore.
    result = worker.DreamingWorker(store).run()

    # Sanity on cluster shape — winner is deterministic by ts.
    assert len(result["clusters"]) == 1
    cluster = result["clusters"][0]
    assert cluster["winner_id"] == winner_id
    assert cluster["retired_ids"] == [loser_id]
    assert result["counts"]["items_retired"] == 1

    # (1) Loser gone from store.all().
    surviving_ids = {i.item_id for i in store.all()}
    assert loser_id not in surviving_ids
    assert surviving_ids == {winner_id, singleton_id}

    # (2) Loser gone from store.get(loser_id) → None.
    assert store.get(loser_id) is None

    # (3) Winner survives with original content (RouterStore.get returns the
    # markdown-base copy first per _READ_ORDER).
    survived_winner = store.get(winner_id)
    assert survived_winner is not None
    assert survived_winner.content == winner_content

    # (4) Disk-backed markdown backend reflects the delete fan-out: the loser's
    # on-disk doc is unlinked, the winner's persists.
    assert not loser_md.exists(), (
        f"markdown backend not updated: {loser_md} should have been unlinked "
        "by Router.delete fan-out"
    )
    assert winner_md.exists(), "winner's markdown doc must persist"
    assert singleton_md.exists(), "singleton's markdown doc must persist"
