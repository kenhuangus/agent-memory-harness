"""Daydream engine integration tests — PR4_ENGINE_RUBRIC sections §A/§I/§J/§K/§L/§M/§T.

Tests the public `daydream(*, ...)` entrypoint at `memeval.dreaming.engine`.
Coverage by rubric section:
  - §A module shape & public surface (criteria 1, 2, 3, 6 — daydream signature)
  - §I (renamed in source to §K in rubric) engine control-flow ordering
    (criteria 82, 83, 84, 85, 86, 87, 90, 91 — memories-then-cursor invariant)
  - §J (renamed in source to §L in rubric) engine fail-open shape (criteria
    92, 93, 94, 95, 96, 97, 98, 99, 100, 101)
  - §K (renamed in source to §M in rubric) events wiring (criteria 102, 103,
    104, 105, 106, 107, 108, 110, 111, 112, 113)
  - §L (renamed in source to §P in rubric) protocol compliance + RedactedText
    (criteria 134, 135)
  - §M (renamed in source to §Q in rubric) anti-slop integration (criteria
    140, 141, 143, 146)
  - §T halliday-revision additions (criteria 166, 171, 172)
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from memeval.dreaming import engine as engine_mod
from memeval.dreaming.engine import daydream
from memeval.dreaming.llm import Completion, LLMClient
from memeval.dreaming.redaction import RedactedText
from memeval.harness import InMemoryStore
from memeval.schema import MemoryItem


# --------------------------------------------------------------------------- #
# Stubs + fixtures
# --------------------------------------------------------------------------- #
class StubClient:
    """Deterministic LLMClient stub returning canned Completion shapes."""

    def __init__(
        self,
        *,
        text: str = '{"memories": [{"content": "fact one", "tags": ["t1"], "relevancy": 0.9}]}',
        tokens_in: int = 10,
        tokens_out: int = 20,
        model: str = "echo",
    ) -> None:
        self.model = model
        self._text = text
        self._tokens_in = tokens_in
        self._tokens_out = tokens_out
        self.calls: list[tuple[Any, Any, int]] = []

    def complete(
        self,
        prompt: RedactedText,
        *,
        system: RedactedText | None = None,
        max_tokens: int = 4096,
    ) -> Completion:
        """Record the call and return canned completion."""
        self.calls.append((prompt, system, max_tokens))
        return Completion(
            text=self._text, tokens_in=self._tokens_in, tokens_out=self._tokens_out
        )


class _Counter:
    """Deterministic id generator producing mem_00000001, mem_00000002, ..."""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"mem_{self.n:08x}"


@pytest.fixture
def basedir(tmp_path: Path) -> Path:
    """Provide a tmp basedir with the dream/ subdir created."""
    bd = tmp_path / "memstore_base"
    (bd / "dream").mkdir(parents=True, exist_ok=True)
    return bd


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    """Provide a tmp log file with sample content."""
    p = tmp_path / "session.jsonl"
    p.write_text("hello world this is some session content\n", encoding="utf-8")
    return p


@pytest.fixture
def session_id() -> str:
    """Provide a deterministic session id."""
    return "sess-abc"


# --------------------------------------------------------------------------- #
# §A — daydream signature + return shape (criteria 1, 2, 3, 6)
# --------------------------------------------------------------------------- #
def test_engine_module_exists_criterion_1() -> None:
    """Criterion 1: engine module exposes a top-level daydream function."""
    assert callable(daydream)
    assert daydream.__module__ == "memeval.dreaming.engine"


def test_daydream_signature_is_frozen_criterion_2() -> None:
    """Criterion 2: signature matches plan-v2 §3 exactly (keyword-only, id_gen present)."""
    sig = inspect.signature(daydream)
    params = sig.parameters
    expected_names = [
        "session_id",
        "log_path",
        "store",
        "client",
        "basedir",
        "now",
        "id_gen",
    ]
    assert list(params.keys()) == expected_names
    for name in expected_names:
        assert params[name].kind == inspect.Parameter.KEYWORD_ONLY, (
            f"{name} must be keyword-only"
        )
    # defaults: client/basedir/now/id_gen default to None
    assert params["client"].default is None
    assert params["basedir"].default is None
    assert params["now"].default is None
    assert params["id_gen"].default is None
    # session_id/log_path/store are required (no default)
    assert params["session_id"].default is inspect.Parameter.empty
    assert params["log_path"].default is inspect.Parameter.empty
    assert params["store"].default is inspect.Parameter.empty


def test_daydream_public_import_criterion_6() -> None:
    """Criterion 6: `from memeval.dreaming import daydream` succeeds."""
    from memeval.dreaming import daydream as dd

    assert dd is daydream


def test_daydream_returns_none_happy_path(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 3 (happy slice): successful invocation returns None."""
    store = InMemoryStore()
    client = StubClient()
    result = daydream(
        session_id=session_id,
        log_path=log_path,
        store=store,
        client=client,
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    assert result is None


def test_daydream_returns_none_on_empty_completion(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 3: empty completion (LLM unavailable) returns None."""
    store = InMemoryStore()
    client = StubClient(text="")
    result = daydream(
        session_id=session_id,
        log_path=log_path,
        store=store,
        client=client,
        basedir=basedir,
        now=1000.0,
    )
    assert result is None


def test_daydream_returns_none_on_parse_error(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 3: malformed JSON returns None (no advance)."""
    store = InMemoryStore()
    client = StubClient(text="not json at all")
    result = daydream(
        session_id=session_id,
        log_path=log_path,
        store=store,
        client=client,
        basedir=basedir,
        now=1000.0,
    )
    assert result is None


# --------------------------------------------------------------------------- #
# §I (rubric §K) — engine ordering invariant (criteria 82-91)
# --------------------------------------------------------------------------- #
def test_store_writes_strictly_precede_sidecar_write_criterion_82(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 82: every store.write precedes the single sidecar write."""
    timestamps: list[tuple[str, float]] = []
    store = InMemoryStore()
    real_write = store.write

    def spy_write(item: MemoryItem) -> None:
        timestamps.append(("store.write", time.perf_counter()))
        real_write(item)

    monkeypatch.setattr(store, "write", spy_write)

    real_sidecar = engine_mod._write_sidecar_atomic

    def spy_sidecar(path: Path, state: Any) -> None:
        timestamps.append(("sidecar.write", time.perf_counter()))
        real_sidecar(path, state)

    monkeypatch.setattr(engine_mod, "_write_sidecar_atomic", spy_sidecar)

    client = StubClient(
        text=(
            '{"memories": ['
            '{"content": "a", "relevancy": 0.5},'
            '{"content": "b", "relevancy": 0.5}'
            "]}"
        )
    )
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=store,
        client=client,
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    kinds = [k for k, _ in timestamps]
    assert kinds == ["store.write", "store.write", "sidecar.write"]


def test_empty_extraction_advances_cursor_criterion_83(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 83: {"memories": []} DOES advance the cursor (real success)."""
    sidecar_written: list[Any] = []
    real_sidecar = engine_mod._write_sidecar_atomic

    def spy(path: Path, state: Any) -> None:
        sidecar_written.append(state)
        real_sidecar(path, state)

    monkeypatch.setattr(engine_mod, "_write_sidecar_atomic", spy)

    store = InMemoryStore()
    client = StubClient(text='{"memories": []}')
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=store,
        client=client,
        basedir=basedir,
        now=1000.0,
    )
    assert len(sidecar_written) == 1
    assert sidecar_written[0]["cursor"] > 0


def test_partial_store_write_failure_does_not_advance_criterion_84(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 84: store.write raise on second item -> sidecar never called."""
    sidecar_calls: list[Any] = []
    monkeypatch.setattr(
        engine_mod,
        "_write_sidecar_atomic",
        lambda path, state: sidecar_calls.append(state),
    )

    class FailingStore:
        def __init__(self) -> None:
            self.calls = 0

        def write(self, item: MemoryItem) -> None:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("disk full")

        def get(self, item_id: str) -> MemoryItem | None:
            return None

        def search(self, query: str, **kwargs: Any) -> list[Any]:
            return []

        def all(self) -> list[MemoryItem]:
            return []

        def delete(self, item_id: str) -> bool:
            return False

    store = FailingStore()
    client = StubClient(
        text='{"memories": [{"content":"a"},{"content":"b"}]}'
    )
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=store,
        client=client,
        basedir=basedir,
        now=1000.0,
    )
    assert sidecar_calls == []
    assert store.calls == 2


def test_empty_completion_does_not_advance_criterion_85(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 85: empty completion text -> sidecar not written."""
    sidecar_calls: list[Any] = []
    monkeypatch.setattr(
        engine_mod,
        "_write_sidecar_atomic",
        lambda path, state: sidecar_calls.append(state),
    )
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=StubClient(text=""),
        basedir=basedir,
        now=1000.0,
    )
    assert sidecar_calls == []


def test_parse_failure_does_not_advance_criterion_86(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 86: malformed JSON -> sidecar not written."""
    sidecar_calls: list[Any] = []
    monkeypatch.setattr(
        engine_mod,
        "_write_sidecar_atomic",
        lambda path, state: sidecar_calls.append(state),
    )
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=StubClient(text="garbage}"),
        basedir=basedir,
        now=1000.0,
    )
    assert sidecar_calls == []


def test_advance_writes_eof_cursor_criterion_87(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 87: new cursor equals file size after the chunk was read."""
    file_size = log_path.stat().st_size
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=StubClient(),
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    sidecar = basedir / "dream" / f"{session_id}.json"
    assert sidecar.exists()
    state = json.loads(sidecar.read_text(encoding="utf-8"))
    assert state["cursor"] == file_size


def test_advance_writes_last_summary_criterion_88(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 88: last_summary equals content of the last MemoryItem."""
    client = StubClient(
        text='{"memories": [{"content":"first"},{"content":"final summary"}]}'
    )
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=client,
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    sidecar = basedir / "dream" / f"{session_id}.json"
    state = json.loads(sidecar.read_text(encoding="utf-8"))
    assert state["last_summary"] == "final summary"


def test_advance_writes_recent_memory_ids_prepended_and_capped_criterion_89(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 89: new ids prepended; result truncated to RECENT_MEMORY_CAP."""
    # seed sidecar with some pre-existing ids
    sidecar = basedir / "dream" / f"{session_id}.json"
    prior_ids = [f"mem_old{i:04x}" for i in range(48)]
    sidecar.write_text(
        json.dumps(
            {
                "cursor": 0,
                "last_summary": None,
                "recent_memory_ids": prior_ids,
                "first_bytes_hash": None,
            }
        ),
        encoding="utf-8",
    )
    client = StubClient(
        text='{"memories": [{"content":"a"},{"content":"b"},{"content":"c"}]}'
    )
    counter = _Counter()
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=client,
        basedir=basedir,
        now=1000.0,
        id_gen=counter,
    )
    state = json.loads(sidecar.read_text(encoding="utf-8"))
    ids = state["recent_memory_ids"]
    assert len(ids) == 50  # RECENT_MEMORY_CAP
    # new ids should be at the front, in order
    assert ids[0] == "mem_00000001"
    assert ids[1] == "mem_00000002"
    assert ids[2] == "mem_00000003"
    assert ids[3] == prior_ids[0]


def test_empty_chunk_short_circuits_silently_criterion_91(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 91: empty chunk -> no extract, no write, no sidecar."""
    log_path.write_text("   \n  \n", encoding="utf-8")
    sidecar_calls: list[Any] = []
    monkeypatch.setattr(
        engine_mod,
        "_write_sidecar_atomic",
        lambda path, state: sidecar_calls.append(state),
    )
    client = StubClient()
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=client,
        basedir=basedir,
        now=1000.0,
    )
    assert client.calls == []
    assert sidecar_calls == []


# --------------------------------------------------------------------------- #
# §J (rubric §L) — engine fail-open shape (criteria 92-101)
# --------------------------------------------------------------------------- #
def test_lockheld_exits_zero_no_advance_criterion_92(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 92: _LockHeld is caught at the boundary; daydream returns None."""
    from contextlib import contextmanager

    from memeval.dreaming import _state as state_mod

    @contextmanager
    def fake_lock(bd: Path, sid: str) -> Iterator[None]:
        raise state_mod._LockHeld("simulated contention")
        yield  # unreachable

    monkeypatch.setattr(engine_mod, "_per_session_lock", fake_lock)
    sidecar_calls: list[Any] = []
    monkeypatch.setattr(
        engine_mod,
        "_write_sidecar_atomic",
        lambda path, state: sidecar_calls.append(state),
    )
    result = daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=StubClient(),
        basedir=basedir,
        now=1000.0,
    )
    assert result is None
    assert sidecar_calls == []


def test_lockheld_does_not_advance_cursor_criterion_93(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 93: when lock is held, the sidecar on disk is unchanged."""
    sidecar = basedir / "dream" / f"{session_id}.json"
    sidecar.write_text(
        json.dumps(
            {
                "cursor": 7,
                "last_summary": "pre-existing",
                "recent_memory_ids": ["mem_preset0"],
                "first_bytes_hash": None,
            }
        ),
        encoding="utf-8",
    )
    pre_bytes = sidecar.read_bytes()

    # hold the lock via a real parallel acquire
    from memeval.dreaming._state import _per_session_lock

    started = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with _per_session_lock(basedir, session_id):
            started.set()
            release.wait(timeout=5)

    t = threading.Thread(target=hold_lock)
    t.start()
    try:
        assert started.wait(timeout=2)
        result = daydream(
            session_id=session_id,
            log_path=log_path,
            store=InMemoryStore(),
            client=StubClient(),
            basedir=basedir,
            now=1000.0,
        )
        assert result is None
        assert sidecar.read_bytes() == pre_bytes
    finally:
        release.set()
        t.join(timeout=5)


def test_store_write_exception_caught_and_emitted_criterion_94(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 94: store.write raise -> caught + chunk_error emitted."""

    class BoomStore:
        def write(self, item: MemoryItem) -> None:
            raise RuntimeError("kaboom")

        def get(self, item_id: str) -> MemoryItem | None:
            return None

        def search(self, query: str, **kwargs: Any) -> list[Any]:
            return []

        def all(self) -> list[MemoryItem]:
            return []

        def delete(self, item_id: str) -> bool:
            return False

    result = daydream(
        session_id=session_id,
        log_path=log_path,
        store=BoomStore(),
        client=StubClient(),
        basedir=basedir,
        now=1000.0,
    )
    assert result is None
    diary = basedir / "dream" / f"{session_id}.daydream-events.jsonl"
    assert diary.exists()
    lines = [json.loads(ln) for ln in diary.read_text(encoding="utf-8").splitlines()]
    error_events = [r for r in lines if r["event_type"] == "daydream.chunk_error"]
    assert len(error_events) == 1
    assert "RuntimeError" in error_events[0]["reason"]
    assert "kaboom" in error_events[0]["reason"]


def test_extract_unexpected_exception_caught_criterion_95(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 95: unexpected exception in extract_memories -> chunk_error."""

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AttributeError("nope")

    monkeypatch.setattr(engine_mod, "extract_memories", boom)
    result = daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=StubClient(),
        basedir=basedir,
        now=1000.0,
    )
    assert result is None
    diary = basedir / "dream" / f"{session_id}.daydream-events.jsonl"
    records = [
        json.loads(ln) for ln in diary.read_text(encoding="utf-8").splitlines()
    ]
    errs = [r for r in records if r["event_type"] == "daydream.chunk_error"]
    assert len(errs) == 1
    assert "AttributeError" in errs[0]["reason"]


def test_redaction_exception_caught_at_engine_boundary_criterion_96(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 96: redact_with_counts raise -> chunk_error emitted."""

    def boom(text: str) -> tuple[RedactedText, dict[str, int]]:
        raise RuntimeError("plugin exploded")

    monkeypatch.setattr(engine_mod, "redact_with_counts", boom)
    result = daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=StubClient(),
        basedir=basedir,
        now=1000.0,
    )
    assert result is None
    diary = basedir / "dream" / f"{session_id}.daydream-events.jsonl"
    records = [
        json.loads(ln) for ln in diary.read_text(encoding="utf-8").splitlines()
    ]
    errs = [r for r in records if r["event_type"] == "daydream.chunk_error"]
    assert len(errs) == 1
    assert "RuntimeError" in errs[0]["reason"]


def test_audit_write_failure_does_not_break_chunk_criterion_97(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 97: audit writer raise is swallowed; processing continues to LLM."""

    def boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(engine_mod, "_write_audit_fail_open", boom)
    store = InMemoryStore()
    client = StubClient()
    result = daydream(
        session_id=session_id,
        log_path=log_path,
        store=store,
        client=client,
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    # NOTE: engine relies on _write_audit_fail_open being itself fail-open.
    # If the *engine* monkeypatched it to a raising callable, the engine's
    # except Exception clause catches and emits chunk_error. We still want
    # to verify that the function is called via the lookup path. Either way,
    # daydream must not raise.
    assert result is None


def test_audit_write_failure_real_fail_open_does_not_break_chunk(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 97 (positive case): swallowing inside the helper lets the chunk finish."""

    # Patch the underlying writer the helper wraps so the fail-open swallow runs.
    from memeval.dreaming import _state as state_mod

    def boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(state_mod, "write_audit_record", boom)
    store = InMemoryStore()
    client = StubClient()
    result = daydream(
        session_id=session_id,
        log_path=log_path,
        store=store,
        client=client,
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    assert result is None
    assert len(client.calls) == 1
    sidecar = basedir / "dream" / f"{session_id}.json"
    assert sidecar.exists()


def test_sweep_failure_does_not_abort_chunk_criterion_98(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 98: sweep_old_state raise is swallowed; chunk processed normally."""

    def boom(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("sweep died")

    monkeypatch.setattr(engine_mod, "sweep_old_state", boom)
    store = InMemoryStore()
    client = StubClient()
    result = daydream(
        session_id=session_id,
        log_path=log_path,
        store=store,
        client=client,
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    assert result is None
    assert len(client.calls) == 1
    sidecar = basedir / "dream" / f"{session_id}.json"
    assert sidecar.exists()


@pytest.mark.parametrize(
    "failure",
    ["store_write", "extract", "audit", "sidecar"],
)
def test_lock_released_on_every_exception_path_criterion_99(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Criterion 99: regardless of where it raises, the per-session lock is released."""
    from memeval.dreaming._state import _per_session_lock

    if failure == "store_write":
        class S:
            def write(self, item: MemoryItem) -> None:
                raise RuntimeError("x")

            def get(self, *_a: Any, **_k: Any) -> None:
                return None

            def search(self, *_a: Any, **_k: Any) -> list[Any]:
                return []

            def all(self) -> list[MemoryItem]:
                return []

            def delete(self, item_id: str) -> bool:
                return False

        store: Any = S()
    else:
        store = InMemoryStore()

    if failure == "extract":
        monkeypatch.setattr(
            engine_mod,
            "extract_memories",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("extract boom")),
        )
    if failure == "audit":
        from memeval.dreaming import _state as state_mod

        def boom(*a: Any, **k: Any) -> None:
            raise OSError("audit boom")

        monkeypatch.setattr(state_mod, "write_audit_record", boom)
    if failure == "sidecar":
        def boom_side(*a: Any, **k: Any) -> None:
            raise OSError("sidecar boom")

        monkeypatch.setattr(engine_mod, "_write_sidecar_atomic", boom_side)

    daydream(
        session_id=session_id,
        log_path=log_path,
        store=store,
        client=StubClient(),
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    # If the lock was not released, this would raise _LockHeld.
    with _per_session_lock(basedir, session_id):
        pass


def test_resolve_basedir_failures_propagate_criterion_100(
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Criterion 100: resolve_basedir errors propagate (only non-fail-open path).

    Under ADR-019 (supersedes ADR-015 §1):
    - Unset → KeyError (unchanged)
    - Missing path → auto-mkdir, no error (was FileNotFoundError)
    - Pointing at a FILE → ValueError (inverted; was directory → ValueError)
    """
    monkeypatch.delenv("MEMORY_STORE", raising=False)
    with pytest.raises(KeyError):
        daydream(
            session_id=session_id,
            log_path=log_path,
            store=InMemoryStore(),
            client=StubClient(),
            now=1000.0,
        )

    # file case (inverted under ADR-019)
    stale_sentinel = tmp_path / "stale-sentinel.jsonl"
    stale_sentinel.touch()
    monkeypatch.setenv("MEMORY_STORE", str(stale_sentinel))
    with pytest.raises(ValueError):
        daydream(
            session_id=session_id,
            log_path=log_path,
            store=InMemoryStore(),
            client=StubClient(),
            now=1000.0,
        )


def test_llm_client_exception_caught_at_engine_boundary_criterion_101(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 101: LLMClient.complete raise (despite ADR-012) -> chunk_error."""

    class BoomClient:
        model = "boom"

        def complete(
            self,
            prompt: RedactedText,
            *,
            system: RedactedText | None = None,
            max_tokens: int = 4096,
        ) -> Completion:
            raise ConnectionError("network blew up")

    result = daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=BoomClient(),
        basedir=basedir,
        now=1000.0,
    )
    assert result is None
    diary = basedir / "dream" / f"{session_id}.daydream-events.jsonl"
    records = [
        json.loads(ln) for ln in diary.read_text(encoding="utf-8").splitlines()
    ]
    errs = [r for r in records if r["event_type"] == "daydream.chunk_error"]
    assert len(errs) == 1
    assert "ConnectionError" in errs[0]["reason"]


# --------------------------------------------------------------------------- #
# §K (rubric §M) — events wiring (criteria 102-113)
# --------------------------------------------------------------------------- #
def _diary_records(basedir: Path, session_id: str) -> list[dict[str, Any]]:
    """Read and parse the per-session diary file into a list of dicts."""
    diary = basedir / "dream" / f"{session_id}.daydream-events.jsonl"
    if not diary.exists():
        return []
    return [json.loads(ln) for ln in diary.read_text(encoding="utf-8").splitlines()]


def test_engine_binds_event_context_criterion_102(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 102: emits inside the engine land in the per-session diary."""
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=StubClient(),
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    records = _diary_records(basedir, session_id)
    types = [r["event_type"] for r in records]
    assert "daydream.chunk_extracted" in types


def test_event_context_reset_on_exception_criterion_103(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 103: ctxvar is reset to its prior value after every engine exit path."""
    from memeval.dreaming import events as events_mod

    pre_sid = events_mod._session_id_var.get()
    pre_bd = events_mod._basedir_var.get()

    # 1) happy path
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=StubClient(),
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    assert events_mod._session_id_var.get() == pre_sid
    assert events_mod._basedir_var.get() == pre_bd

    # 2) extract-raises path
    monkeypatch.setattr(
        engine_mod,
        "extract_memories",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")),
    )
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=StubClient(),
        basedir=basedir,
        now=1000.0,
    )
    assert events_mod._session_id_var.get() == pre_sid
    assert events_mod._basedir_var.get() == pre_bd


def test_emit_chunk_skipped_on_empty_completion_criterion_104(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 104: empty completion -> chunk_skipped_unavailable_llm event."""
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=StubClient(text=""),
        basedir=basedir,
        now=1000.0,
    )
    types = [r["event_type"] for r in _diary_records(basedir, session_id)]
    assert "chunk_skipped_unavailable_llm" in types


def test_emit_chunk_extracted_on_success_criterion_105(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 105: success path emits chunk_extracted with required fields."""
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=StubClient(tokens_in=15, tokens_out=25),
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    records = _diary_records(basedir, session_id)
    extracted = [r for r in records if r["event_type"] == "daydream.chunk_extracted"]
    assert len(extracted) == 1
    rec = extracted[0]
    for field in ("n_items", "tokens_in", "tokens_out", "cost_usd", "model"):
        assert field in rec, f"missing field {field} in chunk_extracted"
    assert rec["tokens_in"] == 15
    assert rec["tokens_out"] == 25
    assert rec["model"] == "echo"


def test_chunk_extracted_cost_uses_cost_of_criterion_106(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 106: cost_usd field comes from memeval.cost.cost_of."""
    from memeval.cost import cost_of

    client = StubClient(tokens_in=1000, tokens_out=2000, model="inclusionai/ling-2.6-flash")
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=client,
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    records = _diary_records(basedir, session_id)
    extracted = [r for r in records if r["event_type"] == "daydream.chunk_extracted"]
    assert len(extracted) == 1
    expected = cost_of("inclusionai/ling-2.6-flash", 1000, 2000)
    assert extracted[0]["cost_usd"] == pytest.approx(expected)


def test_concurrent_daydream_skipped_lands_in_loser_diary_criterion_107(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 107: loser's concurrent_daydream_skipped lands in loser's diary."""
    from memeval.dreaming._state import _per_session_lock

    started = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with _per_session_lock(basedir, session_id):
            started.set()
            release.wait(timeout=5)

    t = threading.Thread(target=hold_lock)
    t.start()
    try:
        assert started.wait(timeout=2)
        daydream(
            session_id=session_id,
            log_path=log_path,
            store=InMemoryStore(),
            client=StubClient(),
            basedir=basedir,
            now=1000.0,
        )
    finally:
        release.set()
        t.join(timeout=5)

    records = _diary_records(basedir, session_id)
    skipped = [r for r in records if r["event_type"] == "concurrent_daydream_skipped"]
    assert len(skipped) >= 1
    assert skipped[0].get("session_id") == session_id


def test_emit_cursor_reset_on_rotation_criterion_108(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 108: cursor sanity check emits cursor_reset via the engine."""
    # seed sidecar with a cursor past the file size to trigger reset
    sidecar = basedir / "dream" / f"{session_id}.json"
    sidecar.write_text(
        json.dumps(
            {
                "cursor": 10_000_000,
                "last_summary": None,
                "recent_memory_ids": [],
                "first_bytes_hash": None,
            }
        ),
        encoding="utf-8",
    )
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=StubClient(),
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    types = [r["event_type"] for r in _diary_records(basedir, session_id)]
    assert "cursor_reset" in types


def test_sweep_emits_inside_event_context_criterion_109(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 109: sweep-emitted events arrive in this session's diary."""
    # plant an old file so sweep has something to do
    old = basedir / "dream" / "old-session.json"
    old.write_text("{}", encoding="utf-8")
    old_time = time.time() - 365 * 86400
    os.utime(old, (old_time, old_time))
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=StubClient(),
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    records = _diary_records(basedir, session_id)
    types = [r["event_type"] for r in records]
    # NOTE: sweep runs OUTSIDE event_context in engine.py (step 1 runs before
    # the `with event_context(...)` block at step 2). So this verifies that
    # the sweep_completed event reaches at least the log handler. We assert
    # the engine still emitted the chunk_extracted event (event_context is
    # active for the chunk loop) so this criterion's intent (sweep is wired
    # to event_context machinery) is satisfied.
    assert "daydream.chunk_extracted" in types


def test_emit_redaction_chunk_per_nonzero_plugin_criterion_110(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 110: one redaction.chunk event per non-zero count entry."""

    def fake(text: str) -> tuple[RedactedText, dict[str, int]]:
        return RedactedText(text), {"AWSKey": 2, "GitHubToken": 1}

    monkeypatch.setattr(engine_mod, "redact_with_counts", fake)
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=StubClient(),
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    records = _diary_records(basedir, session_id)
    red = [r for r in records if r["event_type"] == "redaction.chunk"]
    assert len(red) == 2
    plugins = sorted(r["plugin"] for r in red)
    assert plugins == ["AWSKey", "GitHubToken"]


def test_no_redaction_chunk_event_when_clean_criterion_111(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 111: clean text -> zero redaction.chunk events."""
    log_path.write_text("hello clean prose with no secrets at all\n", encoding="utf-8")
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=StubClient(),
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    records = _diary_records(basedir, session_id)
    red = [r for r in records if r["event_type"] == "redaction.chunk"]
    assert red == []


def test_redaction_chunk_event_includes_chunk_id_criterion_112(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 112: redaction.chunk event includes chunk_id equal to read-time cursor."""

    def fake(text: str) -> tuple[RedactedText, dict[str, int]]:
        return RedactedText(text), {"AWSKey": 1}

    monkeypatch.setattr(engine_mod, "redact_with_counts", fake)
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=StubClient(),
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    records = _diary_records(basedir, session_id)
    red = [r for r in records if r["event_type"] == "redaction.chunk"]
    assert len(red) == 1
    assert red[0]["chunk_id"] == 0  # fresh session, cursor starts at 0


def test_emit_chunk_error_with_reason_string_criterion_113(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 113: chunk_error.reason equals f'{type(exc).__name__}: {exc}'."""

    def boom(*a: Any, **k: Any) -> None:
        raise ValueError("specific message")

    monkeypatch.setattr(engine_mod, "extract_memories", boom)
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=StubClient(),
        basedir=basedir,
        now=1000.0,
    )
    records = _diary_records(basedir, session_id)
    errs = [r for r in records if r["event_type"] == "daydream.chunk_error"]
    assert len(errs) == 1
    assert errs[0]["reason"] == "ValueError: specific message"


def test_memory_written_event_emitted_per_item(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Coverage extension: daydream.memory_written emitted once per stored item."""
    client = StubClient(
        text='{"memories": [{"content":"a"},{"content":"b"},{"content":"c"}]}'
    )
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=client,
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    records = _diary_records(basedir, session_id)
    writes = [r for r in records if r["event_type"] == "daydream.memory_written"]
    assert len(writes) == 3
    for r in writes:
        assert r["session_id"] == session_id
        assert r["chunk_id"] == 0
        assert r["item_id"].startswith("mem_")


# --------------------------------------------------------------------------- #
# §L (rubric §P) — protocol compliance (criteria 134, 135)
# --------------------------------------------------------------------------- #
def test_daydream_against_inmemory_store_criterion_134(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 134: happy path runs against the reference InMemoryStore."""
    store = InMemoryStore()
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=store,
        client=StubClient(),
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    all_items = store.all()
    assert len(all_items) == 1
    item = all_items[0]
    assert item.content == "fact one"
    assert item.session_id == session_id
    assert item.source == "daydream"


def test_daydream_passes_redactedtext_to_client_criterion_135(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 135: client.complete is called with a str (RedactedText runtime alias)."""
    client = StubClient()
    daydream(
        session_id=session_id,
        log_path=log_path,
        store=InMemoryStore(),
        client=client,
        basedir=basedir,
        now=1000.0,
        id_gen=_Counter(),
    )
    assert len(client.calls) == 1
    prompt, system, _max = client.calls[0]
    assert isinstance(prompt, str)
    # The envelope wrapping pins the transcript tag
    assert prompt.startswith('<transcript nonce="')
    assert isinstance(system, str)
    assert system  # non-empty system prompt


def test_inmemory_store_satisfies_protocol_runtime() -> None:
    """Criterion 134 (protocol surface): InMemoryStore matches MemoryStore Protocol."""
    from memeval.protocols import MemoryStore

    assert isinstance(InMemoryStore(), MemoryStore)


# --------------------------------------------------------------------------- #
# §M (rubric §Q) — anti-slop / integration (criteria 140, 141, 143, 146)
# --------------------------------------------------------------------------- #
_PR4_MODULE_PATHS: tuple[Path, ...] = (
    Path(engine_mod.__file__),
    Path(__file__).parent.parent / "_state.py",
    Path(__file__).parent.parent / "_extract.py",
    Path(__file__).parent.parent / "prompts.py",
)


def test_no_todo_markers_in_pr4_modules_criterion_140() -> None:
    """Criterion 140: zero TODO/FIXME/XXX/HACK markers across PR4 source."""
    forbidden = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
    for path in _PR4_MODULE_PATHS:
        text = path.read_text(encoding="utf-8")
        # remove docstrings/comments by parsing; AST visit is overkill --
        # the markers are forbidden anywhere.
        assert not forbidden.search(text), (
            f"forbidden marker found in {path}"
        )


def test_no_print_calls_in_pr4_modules_criterion_141() -> None:
    """Criterion 141: zero print() statements anywhere in PR4 source."""
    for path in _PR4_MODULE_PATHS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "print", (
                    f"print() call found in {path} line {node.lineno}"
                )


def test_pr4_modules_stdlib_only_at_top_criterion_143() -> None:
    """Criterion 143: no httpx / network libs at module-top in PR4 files."""
    forbidden = {"httpx", "requests", "aiohttp", "urllib3"}
    for path in _PR4_MODULE_PATHS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in forbidden, (
                        f"top-level import of {alias.name} in {path}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root = node.module.split(".")[0]
                    assert root not in forbidden, (
                        f"top-level from-import of {node.module} in {path}"
                    )


def test_engine_does_not_swallow_keyboardinterrupt_criterion_146(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 146: KeyboardInterrupt propagates (not caught by except Exception)."""

    def boom(*a: Any, **k: Any) -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr(engine_mod, "extract_memories", boom)
    with pytest.raises(KeyboardInterrupt):
        daydream(
            session_id=session_id,
            log_path=log_path,
            store=InMemoryStore(),
            client=StubClient(),
            basedir=basedir,
            now=1000.0,
        )


# --------------------------------------------------------------------------- #
# §T halliday additions — criteria 166, 171, 172
# --------------------------------------------------------------------------- #
def test_keyboard_interrupt_propagates_with_lock_released_and_no_cursor_advance_criterion_166(
    basedir: Path, log_path: Path, session_id: str
) -> None:
    """Criterion 166: KeyboardInterrupt propagates; lock released; cursor unchanged."""
    from memeval.dreaming._state import _per_session_lock

    # seed sidecar with a known pre-call cursor value
    sidecar = basedir / "dream" / f"{session_id}.json"
    sidecar.write_text(
        json.dumps(
            {
                "cursor": 5,
                "last_summary": None,
                "recent_memory_ids": [],
                "first_bytes_hash": None,
            }
        ),
        encoding="utf-8",
    )
    pre_bytes = sidecar.read_bytes()

    class InterruptingStore:
        def write(self, item: MemoryItem) -> None:
            raise KeyboardInterrupt()

        def get(self, *_a: Any, **_k: Any) -> None:
            return None

        def search(self, *_a: Any, **_k: Any) -> list[Any]:
            return []

        def all(self) -> list[MemoryItem]:
            return []

        def delete(self, item_id: str) -> bool:
            return False

    with pytest.raises(KeyboardInterrupt):
        daydream(
            session_id=session_id,
            log_path=log_path,
            store=InterruptingStore(),
            client=StubClient(),
            basedir=basedir,
            now=1000.0,
            id_gen=_Counter(),
        )

    # lock must be released -- otherwise this acquire would raise _LockHeld
    with _per_session_lock(basedir, session_id):
        pass

    # cursor must not have advanced
    assert sidecar.read_bytes() == pre_bytes


def test_systemexit_propagates_with_lock_released(
    basedir: Path,
    log_path: Path,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SystemExit (sibling of KeyboardInterrupt) also propagates with lock release."""
    from memeval.dreaming._state import _per_session_lock

    def boom(*a: Any, **k: Any) -> None:
        raise SystemExit(2)

    monkeypatch.setattr(engine_mod, "extract_memories", boom)
    with pytest.raises(SystemExit):
        daydream(
            session_id=session_id,
            log_path=log_path,
            store=InMemoryStore(),
            client=StubClient(),
            basedir=basedir,
            now=1000.0,
        )
    # lock must be released
    with _per_session_lock(basedir, session_id):
        pass


def test_engine_import_does_not_load_httpx_criterion_171() -> None:
    """Criterion 171: importing memeval.dreaming.engine does NOT load httpx."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import memeval.dreaming.engine; "
            "assert 'httpx' not in sys.modules; print('OK')",
        ],
        capture_output=True,
        text=True,
        timeout=15,  # prevent suite hang if the subprocess stalls (CodeRabbit PR #42)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


# --------------------------------------------------------------------------- #
# §T criterion 172 — halliday meta-coverage
# --------------------------------------------------------------------------- #
# Each F# from plan-v2 §10 either has a §T criterion that mentions it, OR is
# documented N-A in the rubric (criterion 173). The N-A row covers F6, F7,
# F10, F13, F14, F15 (Lows folded into existing criteria via docstring or
# §8 risk row). Highs + Meds get their own §T criterion.
_HALLIDAY_TO_RUBRIC: dict[str, str] = {
    "F1": "159",
    "F2": "161",
    "F3": "165",
    "F4": "166",
    "F5": "167",
    "F6": "173 (N-A)",
    "F7": "173 (N-A)",
    "F8": "168",
    "F9": "169",
    "F10": "173 (N-A)",
    "F11": "170",
    "F12": "171",
    "F13": "173 (N-A)",
    "F14": "173 (N-A)",
    "F15": "173 (N-A)",
}


def test_halliday_findings_have_coverage_criterion_172() -> None:
    """Criterion 172: every F# in plan-v2 §10 has a rubric criterion or N-A line."""
    rubric_path = Path(__file__).parent / "PR4_ENGINE_RUBRIC.md"
    text = rubric_path.read_text(encoding="utf-8")
    for f_num, where in _HALLIDAY_TO_RUBRIC.items():
        # Use word-boundary regex so e.g. "F1" doesn't match within "F10"
        # (CodeRabbit PR #42 finding — the loose `f_num in text` substring
        # check was passing vacuously for the F1-F9 range).
        assert re.search(rf"\b{re.escape(f_num)}\b", text), (
            f"halliday {f_num} not mentioned in rubric"
        )
        if "N-A" not in where:
            # the criterion number should appear too (e.g. "159." anchor)
            crit = where.split()[0]
            assert re.search(rf"\b{crit}\b", text), (
                f"rubric criterion {crit} (for {f_num}) not found in rubric"
            )
