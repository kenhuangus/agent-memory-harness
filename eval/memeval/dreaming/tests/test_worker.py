"""Worker tests — every unit-test criterion from INITIAL_DREAM_RUBRIC.md §A-L.

The detection-only Job-1 worker: walk ``store.all()``, group by normalized
content key, return a JSON-serializable summary dict, emit ``dream.summary``.

Shell-command criteria (§A4, §F5, §H3, §J1, §J2, §J3, §K5, §L1) are run
verbatim from the rubric and not duplicated here.

Imports stay stdlib-only at module top (pytest aside) per the dreaming
package's discipline.
"""

from __future__ import annotations

import io
import json
import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from memeval.dreaming import cli, worker
from memeval.harness import InMemoryStore
from memeval.schema import MemoryItem


# --------------------------------------------------------------------------- #
# Shared helpers + fixtures
# --------------------------------------------------------------------------- #


def _item(content: Any, item_id: str | None = None, **overrides: Any) -> MemoryItem:
    """Build a MemoryItem with sensible defaults; counter-based id if unspecified."""
    if item_id is None:
        item_id = f"item-{_item._counter}"  # type: ignore[attr-defined]
        _item._counter += 1  # type: ignore[attr-defined]
    return MemoryItem(item_id=item_id, content=content, **overrides)


_item._counter = 0  # type: ignore[attr-defined]


def _store_with(*contents: Any) -> InMemoryStore:
    """Build an InMemoryStore seeded with one item per `contents` arg."""
    store = InMemoryStore()
    for i, c in enumerate(contents):
        store.write(MemoryItem(item_id=f"i{i}", content=c))
    return store


def _none_content_store(*other_contents: str) -> InMemoryStore:
    """Build an InMemoryStore where the first item has content forcibly set to None.

    ``MemoryItem.content`` is typed ``str`` but Python doesn't enforce that at
    runtime. ``slots=True`` blocks new attributes, not in-place writes; we set
    the slot directly via ``object.__setattr__`` (regular attribute assignment
    works too on slots, but this makes the bypass explicit).
    """
    store = InMemoryStore()
    item = MemoryItem(item_id="none-item", content="placeholder")
    object.__setattr__(item, "content", None)
    store.write(item)
    for i, c in enumerate(other_contents):
        store.write(MemoryItem(item_id=f"other-{i}", content=c))
    return store


@pytest.fixture
def spy_emit(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Capture every memeval.dreaming.events.emit call as (event_type, kwargs)."""
    captured: list[tuple[str, dict[str, Any]]] = []

    def _fake(event_type: str, **fields: Any) -> None:
        """Spy replacement for ``events.emit`` — records the call."""
        captured.append((event_type, fields))

    monkeypatch.setattr("memeval.dreaming.worker.emit", _fake)
    return captured


@pytest.fixture
def memory_store_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temp MEMORY_STORE directory and set the env-var (ADR-019)."""
    store = tmp_path / "memory-store"
    store.mkdir()
    monkeypatch.setenv("MEMORY_STORE", str(store))
    return store


@pytest.fixture
def fake_make_store(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace cli._make_store so CLI tests don't build a real RouterStore."""
    fake_store = InMemoryStore()
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


def test_run_returns_dict_for_single_item() -> None:
    """A1 — run() over a one-item store returns a dict and does not raise."""
    store = _store_with("hello")
    result = worker.DreamingWorker(store).run()
    assert isinstance(result, dict)


def test_run_empty_store_returns_dict() -> None:
    """A2 — run() over an empty store returns a dict and does not raise."""
    store = InMemoryStore()
    result = worker.DreamingWorker(store).run()
    assert isinstance(result, dict)


def test_dream_wrapper_matches_worker() -> None:
    """A3 — module-level worker.dream(store) returns the same dict as DreamingWorker(store).run()."""
    store = _store_with("a", "b", "a")
    assert worker.dream(store) == worker.DreamingWorker(store).run()


# --------------------------------------------------------------------------- #
# §B — Dict shape
# --------------------------------------------------------------------------- #

_EXPECTED_TOP_LEVEL_KEYS = {
    "schema", "version", "mode", "jobs_run",
    "skipped_jobs", "counts", "clusters",
}


def test_run_top_level_keys_exact() -> None:
    """B1 — top-level key set is exactly the pinned set; no extras."""
    result = worker.DreamingWorker(_store_with("x")).run()
    assert set(result.keys()) == _EXPECTED_TOP_LEVEL_KEYS


def test_run_schema_literal() -> None:
    """B2 — schema == 'dream.summary'."""
    result = worker.DreamingWorker(_store_with("x")).run()
    assert result["schema"] == "dream.summary"


def test_run_version_literal() -> None:
    """B3 — version == 1 and is an int (not bool)."""
    result = worker.DreamingWorker(_store_with("x")).run()
    assert result["version"] == 1
    assert type(result["version"]) is int


def test_run_mode_literal() -> None:
    """B4 — mode == 'detection'."""
    result = worker.DreamingWorker(_store_with("x")).run()
    assert result["mode"] == "detection"


def test_run_jobs_run_literal() -> None:
    """B5 — jobs_run == ['dedup_detection']."""
    result = worker.DreamingWorker(_store_with("x")).run()
    assert result["jobs_run"] == ["dedup_detection"]


def test_run_skipped_jobs_literal() -> None:
    """B6 — skipped_jobs list-equal in pinned order."""
    result = worker.DreamingWorker(_store_with("x")).run()
    assert result["skipped_jobs"] == [
        "dedup_merge",
        "contradiction_resolution",
        "governance",
        "pruning",
    ]


def test_run_counts_shape() -> None:
    """B7 — counts has pinned keys and every value is an int (not bool, not float)."""
    result = worker.DreamingWorker(_store_with("x", "y")).run()
    assert set(result["counts"].keys()) == {
        "total_items", "duplicate_clusters", "items_in_duplicates",
    }
    for v in result["counts"].values():
        assert type(v) is int


def test_run_cluster_element_shape() -> None:
    """B8 — clusters is a list of dicts with pinned key set and types; count == len(item_ids)."""
    result = worker.DreamingWorker(_store_with("foo", "foo", "bar", "bar", "bar")).run()
    assert isinstance(result["clusters"], list)
    for cluster in result["clusters"]:
        assert set(cluster.keys()) == {"normalized_key", "item_ids", "count"}
        assert isinstance(cluster["normalized_key"], str)
        assert isinstance(cluster["item_ids"], list)
        assert all(isinstance(i, str) for i in cluster["item_ids"])
        assert type(cluster["count"]) is int
        assert cluster["count"] == len(cluster["item_ids"])


def test_run_result_json_roundtrip() -> None:
    """B9 — result round-trips through json.dumps/loads with equality preserved."""
    result = worker.DreamingWorker(_store_with("a", "a", "b")).run()
    assert json.loads(json.dumps(result)) == result


# --------------------------------------------------------------------------- #
# §C — Counts arithmetic consistency
# --------------------------------------------------------------------------- #


def test_counts_total_items_matches_store() -> None:
    """C1 — counts.total_items equals len(store.all())."""
    store = _store_with("a", "b", "c", "d", "a")
    result = worker.DreamingWorker(store).run()
    assert result["counts"]["total_items"] == len(store.all())


def test_counts_duplicate_clusters_matches_len() -> None:
    """C2 — counts.duplicate_clusters equals len(result.clusters)."""
    result = worker.DreamingWorker(_store_with("a", "a", "b", "b", "b", "c")).run()
    assert result["counts"]["duplicate_clusters"] == len(result["clusters"])


def test_counts_items_in_duplicates_matches_sum() -> None:
    """C3 — counts.items_in_duplicates equals sum of every cluster's count."""
    result = worker.DreamingWorker(_store_with("a", "a", "b", "b", "b", "c")).run()
    assert result["counts"]["items_in_duplicates"] == sum(c["count"] for c in result["clusters"])


def test_clusters_have_count_at_least_two() -> None:
    """C4 — every cluster has count >= 2 (singletons excluded)."""
    result = worker.DreamingWorker(_store_with("a", "b", "a", "c")).run()
    for cluster in result["clusters"]:
        assert cluster["count"] >= 2


# --------------------------------------------------------------------------- #
# §D — Determinism / idempotence
# --------------------------------------------------------------------------- #


def _structural_compare(a: dict, b: dict) -> bool:
    """Structural equality per rubric §D definition.

    Counts and scalar fields compare under ==; clusters compare as a set of
    frozenset(item_ids) plus a multiset of (normalized_key, count) pairs.
    """
    if a.keys() != b.keys():
        return False
    for k in ("schema", "version", "mode", "jobs_run", "skipped_jobs", "counts"):
        if a[k] != b[k]:
            return False
    a_sets = {frozenset(c["item_ids"]) for c in a["clusters"]}
    b_sets = {frozenset(c["item_ids"]) for c in b["clusters"]}
    if a_sets != b_sets:
        return False
    a_pairs = sorted((c["normalized_key"], c["count"]) for c in a["clusters"])
    b_pairs = sorted((c["normalized_key"], c["count"]) for c in b["clusters"])
    return a_pairs == b_pairs


def test_run_idempotent_structural_equal() -> None:
    """D1 — two consecutive runs over unmutated store are structurally equal."""
    store = _store_with("foo", "foo", "bar", "baz", "baz")
    w = worker.DreamingWorker(store)
    assert _structural_compare(w.run(), w.run())


def test_run_idempotent_counts_equal() -> None:
    """D2 — counts dicts are == across consecutive runs."""
    store = _store_with("a", "a", "b")
    w = worker.DreamingWorker(store)
    assert w.run()["counts"] == w.run()["counts"]


def test_no_item_id_in_two_clusters() -> None:
    """D3 — no item_id appears in more than one cluster."""
    result = worker.DreamingWorker(_store_with("a", "a", "b", "b", "c", "c")).run()
    flat = [i for c in result["clusters"] for i in c["item_ids"]]
    assert len(flat) == len(set(flat))


def test_no_duplicate_ids_within_cluster() -> None:
    """D4 — item_ids list inside each cluster contains no duplicate ids."""
    result = worker.DreamingWorker(_store_with("a", "a", "a", "b", "b")).run()
    for cluster in result["clusters"]:
        assert len(cluster["item_ids"]) == len(set(cluster["item_ids"]))


# --------------------------------------------------------------------------- #
# §E — Normalization correctness
# --------------------------------------------------------------------------- #


def test_normalization_positive_punct_and_case() -> None:
    """E1 — 'Hello, world!' and 'hello world' cluster together."""
    store = _store_with("Hello, world!", "hello world")
    result = worker.DreamingWorker(store).run()
    assert len(result["clusters"]) == 1
    assert result["clusters"][0]["count"] == 2
    assert set(result["clusters"][0]["item_ids"]) == {"i0", "i1"}


def test_normalization_negative_different_content() -> None:
    """E2 — 'Hello world.' and 'Hi there' do not cluster."""
    store = _store_with("Hello world.", "Hi there")
    result = worker.DreamingWorker(store).run()
    assert result["clusters"] == []


def test_normalization_whitespace_collapse() -> None:
    """E3 — runs of whitespace collapse to a single space."""
    store = _store_with("foo   bar", "foo bar")
    result = worker.DreamingWorker(store).run()
    assert len(result["clusters"]) == 1
    assert result["clusters"][0]["count"] == 2


def test_normalization_strip_edges() -> None:
    """E4 — leading/trailing whitespace stripped."""
    store = _store_with("  foo bar  ", "foo bar")
    result = worker.DreamingWorker(store).run()
    assert len(result["clusters"]) == 1
    assert result["clusters"][0]["count"] == 2


def test_normalization_three_member_cluster() -> None:
    """E5 — three near-duplicates form one cluster with count 3."""
    store = _store_with("Hello!", "hello", "Hello, ")
    result = worker.DreamingWorker(store).run()
    assert len(result["clusters"]) == 1
    assert result["clusters"][0]["count"] == 3
    assert set(result["clusters"][0]["item_ids"]) == {"i0", "i1", "i2"}


def test_normalization_empty_content_does_not_raise() -> None:
    """E6 — empty content does not raise."""
    store = _store_with("")
    worker.DreamingWorker(store).run()


def test_normalization_none_content_does_not_raise() -> None:
    """E7 — content == None does not raise; coerced to '' per implementer contract."""
    store = _none_content_store("")  # plus another item with empty content
    result = worker.DreamingWorker(store).run()
    # Both items normalize to "" — verify well-formed cluster (if formed).
    assert result["counts"]["total_items"] == 2
    if result["clusters"]:
        cluster = result["clusters"][0]
        assert set(cluster["item_ids"]) == {"none-item", "other-0"}
        assert cluster["count"] == 2


# --------------------------------------------------------------------------- #
# §F — No mutation
# --------------------------------------------------------------------------- #


def _snapshot_store(store: InMemoryStore) -> set[tuple[str, int]]:
    """Return ``{(item_id, version)}`` for every item — the §F1 invariant snapshot."""
    return {(item.item_id, item.version) for item in store.all()}


def test_run_does_not_mutate_id_version_set() -> None:
    """F1 — {(item_id, version) for item in store.all()} unchanged by run()."""
    store = _store_with("a", "a", "b")
    before = _snapshot_store(store)
    worker.DreamingWorker(store).run()
    assert _snapshot_store(store) == before


def test_run_does_not_mutate_content() -> None:
    """F2 — every item's content is byte-identical before and after."""
    store = _store_with("Hello, world!", "hello world")
    before = {item.item_id: item.content for item in store.all()}
    worker.DreamingWorker(store).run()
    after = {iid: store.get(iid).content for iid in before}
    assert before == after


def test_run_does_not_mutate_relevancy() -> None:
    """F3 — every item's relevancy is float-equal before and after (no soft-delete)."""
    store = InMemoryStore()
    store.write(MemoryItem(item_id="a", content="x", relevancy=0.7))
    store.write(MemoryItem(item_id="b", content="x", relevancy=0.4))
    before = {item.item_id: item.relevancy for item in store.all()}
    worker.DreamingWorker(store).run()
    after = {iid: store.get(iid).relevancy for iid in before}
    assert before == after


class _SpyStore:
    """Minimal MemoryStore satisfying the protocol; counts write() calls."""

    def __init__(self, items: list[MemoryItem]) -> None:
        """Seed the spy with an initial item set; ``write_calls`` starts empty."""
        self._items = {i.item_id: i for i in items}
        self.write_calls: list[MemoryItem] = []

    def write(self, item: MemoryItem) -> None:
        """Record the call (the §F4 invariant guard) and stash the item."""
        self.write_calls.append(item)
        self._items[item.item_id] = item

    def get(self, item_id: str) -> MemoryItem | None:
        """Protocol-satisfier: return the item or ``None``."""
        return self._items.get(item_id)

    def search(self, query: str, *, k: int = 5, as_of: float | None = None, **kw: Any) -> list:
        """Protocol-satisfier: search is unused in §F tests; return empty."""
        return []

    def all(self) -> list[MemoryItem]:
        """Protocol-satisfier: return every seeded item (insertion order not guaranteed)."""
        return list(self._items.values())


def test_run_makes_zero_write_calls() -> None:
    """F4 — spy store receives zero write() calls during run()."""
    items = [MemoryItem(item_id="a", content="x"), MemoryItem(item_id="b", content="x")]
    spy = _SpyStore(items)
    worker.DreamingWorker(spy).run()
    assert spy.write_calls == []


def test_run_does_not_mutate_returned_items() -> None:
    """F6 — items in the list returned by store.all() are not mutated by run()."""
    store = _store_with("Hello, world!", "hello world", "different")
    items = list(store.all())
    before = [(i.item_id, i.content, i.relevancy, i.version) for i in items]
    worker.DreamingWorker(store).run()
    after = [(i.item_id, i.content, i.relevancy, i.version) for i in items]
    assert before == after


# --------------------------------------------------------------------------- #
# §G — trajectories_path
# --------------------------------------------------------------------------- #


def test_run_trajectories_path_none_no_effect() -> None:
    """G1 — run(trajectories_path=None) returns the same dict as run()."""
    store = _store_with("a", "a", "b")
    base = worker.DreamingWorker(store).run()
    with_none = worker.DreamingWorker(store).run(trajectories_path=None)
    assert base == with_none


def test_run_trajectories_path_truthy_raises_valueerror() -> None:
    """G2 — truthy trajectories_path raises ValueError naming the v1 carve-out."""
    store = _store_with("a")
    with pytest.raises(ValueError, match="not consumed in v1"):
        worker.DreamingWorker(store).run(trajectories_path="/path/that/does/not/exist")


def test_run_trajectories_path_empty_string_does_not_raise() -> None:
    """G2-supplement — empty string (falsy) passes through as if None (implementer's truthy-rejection convention).

    Beyond the rubric: rubric §G pins truthy-rejection, not non-None-rejection. An
    empty string is falsy and should not be treated as a caller-supplied path.
    Locks in the convention so a future tightening to `is not None` is caught.
    """
    store = _store_with("a")
    base = worker.DreamingWorker(store).run()
    with_empty = worker.DreamingWorker(store).run(trajectories_path="")
    assert base == with_empty


def test_run_no_filesystem_access_to_trajectories_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """G3 — no filesystem open() is attempted against the trajectories_path string."""
    import builtins
    import pathlib

    bogus = "/path/that/does/not/exist/trajectories"
    open_calls: list[str] = []
    real_open = builtins.open
    real_path_open = pathlib.Path.open

    def _trap_builtin_open(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        """Trap ``builtins.open`` so the test can assert no read against the bogus path."""
        open_calls.append(str(path))
        return real_open(path, *args, **kwargs)

    def _trap_path_open(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        """Trap ``pathlib.Path.open`` so the test can assert no read against the bogus path."""
        open_calls.append(str(self))
        return real_path_open(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _trap_builtin_open)
    monkeypatch.setattr(pathlib.Path, "open", _trap_path_open)

    store = _store_with("a")
    with pytest.raises(ValueError):
        worker.DreamingWorker(store).run(trajectories_path=bogus)

    assert all(bogus not in c for c in open_calls)


# --------------------------------------------------------------------------- #
# §H — CLI boundary fail-open
# --------------------------------------------------------------------------- #


def test_dream_all_exits_zero_on_success(
    empty_stdin: None, memory_store_dir: Path, fake_make_store: MagicMock,
) -> None:
    """H1 — daydream-cli dream --all exits 0 on a successful run() call."""
    assert cli.main(["dream", "--all"]) == 0


def test_dream_all_failopens_on_runtime_error(
    empty_stdin: None, memory_store_dir: Path, fake_make_store: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H2 — RuntimeError inside worker → CLI exit 0 + daydream.dream_all_error event."""
    captured: list[tuple[str, dict[str, Any]]] = []

    def _fake_emit(event_type: str, **fields: Any) -> None:
        """Spy replacement for ``events.emit`` — records ``(event_type, fields)``."""
        captured.append((event_type, fields))

    from memeval.dreaming import events as events_mod
    monkeypatch.setattr(events_mod, "emit", _fake_emit)

    def _boom(*args, **kw):
        """Stand-in for ``worker.dream`` that simulates an internal RuntimeError."""
        raise RuntimeError("boom")

    monkeypatch.setattr(worker, "dream", _boom)

    assert cli.main(["dream", "--all"]) == 0
    assert any(et == "daydream.dream_all_error" for et, _ in captured)


def test_dream_all_does_not_swallow_keyboardinterrupt(
    empty_stdin: None, memory_store_dir: Path, fake_make_store: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H4 — KeyboardInterrupt inside worker propagates out of cli.main."""
    def _kbi(*args, **kw):
        """Stand-in for ``worker.dream`` that simulates a user-triggered ^C."""
        raise KeyboardInterrupt
    monkeypatch.setattr(worker, "dream", _kbi)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["dream", "--all"])


def test_dream_all_does_not_swallow_systemexit(
    empty_stdin: None, memory_store_dir: Path, fake_make_store: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H5 — SystemExit inside worker propagates out of cli.main."""
    def _se(*args, **kw):
        """Stand-in for ``worker.dream`` that simulates ``sys.exit(7)`` from a lazy-imported dep."""
        raise SystemExit(7)
    monkeypatch.setattr(worker, "dream", _se)
    with pytest.raises(SystemExit):
        cli.main(["dream", "--all"])


# --------------------------------------------------------------------------- #
# §I — Observability
# --------------------------------------------------------------------------- #


def test_run_emits_exactly_one_event(spy_emit: list[tuple[str, dict[str, Any]]]) -> None:
    """I1 — exactly one events.emit call per successful run()."""
    worker.DreamingWorker(_store_with("a", "a", "b")).run()
    assert len(spy_emit) == 1


def test_run_emit_event_name_literal(spy_emit: list[tuple[str, dict[str, Any]]]) -> None:
    """I2 — emitted event name is the literal 'dream.summary'."""
    worker.DreamingWorker(_store_with("a")).run()
    assert spy_emit[0][0] == "dream.summary"


def test_run_emit_event_required_fields(spy_emit: list[tuple[str, dict[str, Any]]]) -> None:
    """I3 — emit kwargs include mode, total_items, duplicate_clusters."""
    worker.DreamingWorker(_store_with("a", "a", "b")).run()
    kwargs = spy_emit[0][1]
    for key in ("mode", "total_items", "duplicate_clusters"):
        assert key in kwargs


def test_run_emit_event_values_match_summary(spy_emit: list[tuple[str, dict[str, Any]]]) -> None:
    """I4 — emit kwarg values match the returned dict's corresponding fields."""
    result = worker.DreamingWorker(_store_with("a", "a", "b", "b", "b")).run()
    kwargs = spy_emit[0][1]
    assert kwargs["mode"] == result["mode"]
    assert kwargs["total_items"] == result["counts"]["total_items"]
    assert kwargs["duplicate_clusters"] == result["counts"]["duplicate_clusters"]


# --------------------------------------------------------------------------- #
# §L — Concurrency carve-out
# --------------------------------------------------------------------------- #


def test_run_concurrent_threads_same_store() -> None:
    """L2 — two concurrent run() calls produce structurally-equal results to one sequential run."""
    store = _store_with("a", "a", "b", "b", "c")
    sequential = worker.DreamingWorker(store).run()

    results: dict[int, dict] = {}

    def _runner(idx: int) -> None:
        """Thread target — record this run's summary under ``idx`` for the join compare."""
        results[idx] = worker.DreamingWorker(store).run()

    t1 = threading.Thread(target=_runner, args=(1,))
    t2 = threading.Thread(target=_runner, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert _structural_compare(results[1], sequential)
    assert _structural_compare(results[2], sequential)
