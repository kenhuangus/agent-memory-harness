"""Events shim tests — ADR-dreaming-009 (PR3 full impl).

Covers:
  - emit() never raises (fail-open contract, all paths)
  - log-only fallback when no event_context is bound
  - diary write when event_context is bound — file shape, append mode,
    parent-dir creation, no network during write
  - event_context() ContextVar semantics — nested binding, reset on exit
  - diary_path_for() composition
  - fail-open on diary write errors (caller never sees the failure)
  - PR2 surface still works (the OpenRouterClient call sites in
    eval/memeval/dreaming/llm.py don't set context but still call emit)
"""

from __future__ import annotations

import builtins
import json
import logging
import socket
import threading
from pathlib import Path

import pytest

from memeval.dreaming.events import diary_path_for, emit, event_context


# --- emit() basic no-raise -------------------------------------------- #
def test_emit_does_not_raise_with_no_fields():
    emit("simple_event")


def test_emit_does_not_raise_with_kwargs():
    emit("event_with_fields", a=1, b="x", c=None, d=[1, 2, 3])


def test_emit_logs_at_debug_with_event_type(caplog):
    caplog.set_level(logging.DEBUG, logger="memeval.dreaming.events")
    emit("my_event", k="v")
    msgs = [r.getMessage() for r in caplog.records]
    assert any("my_event" in m and "'k': 'v'" in m for m in msgs)


def test_emit_makes_no_network_connect_without_context(monkeypatch):
    """Log-only path: no diary file, no network."""

    def _no_connect(self, *args, **kwargs):
        raise AssertionError(f"network connect attempted by emit(): {args!r}")

    monkeypatch.setattr(socket.socket, "connect", _no_connect)
    emit("event_no_context", count=3)


def test_emit_does_not_propagate_errors_from_weird_fields():
    class WeirdReprable:
        def __repr__(self) -> str:
            return "weird"

    emit("event_weird", thing=WeirdReprable())


# --- log-only fallback (no context bound) ----------------------------- #
def test_emit_outside_context_does_not_create_diary(tmp_path: Path):
    """Without event_context(), no diary file is created anywhere."""
    # We're not in a context, so emit() should not write any file.
    emit("untracked_event")
    # tmp_path is empty (no diary writes happened).
    assert not any(tmp_path.rglob("*.jsonl"))


def test_emit_outside_context_still_logs(caplog):
    """The log-only fallback still records the event in stdlib logging."""
    caplog.set_level(logging.DEBUG, logger="memeval.dreaming.events")
    emit("logged_only", x=1)
    assert any("logged_only" in r.getMessage() for r in caplog.records)


# --- diary_path_for() -------------------------------------------------- #
def test_diary_path_for_composition():
    p = diary_path_for("/tmp/store-dir", "sess-abc")
    assert str(p).endswith("/dream/sess-abc.daydream-events.jsonl")


def test_diary_path_for_accepts_path_object(tmp_path: Path):
    p = diary_path_for(tmp_path, "s")
    assert p == tmp_path / "dream" / "s.daydream-events.jsonl"


# --- event_context() + diary writing ----------------------------------- #
def test_emit_in_context_writes_diary_record(tmp_path: Path):
    with event_context(session_id="sess-1", basedir=tmp_path):
        emit("test_event", k="v")
    diary = diary_path_for(tmp_path, "sess-1")
    assert diary.exists()
    [line] = diary.read_text().splitlines()
    rec = json.loads(line)
    assert rec["event_type"] == "test_event"
    assert rec["k"] == "v"
    assert "ts" in rec
    assert isinstance(rec["ts"], (int, float))


def test_emit_in_context_creates_parent_dir(tmp_path: Path):
    """First emit() in a fresh basedir creates <basedir>/dream/."""
    assert not (tmp_path / "dream").exists()
    with event_context(session_id="s", basedir=tmp_path):
        emit("trigger")
    assert (tmp_path / "dream").is_dir()


def test_emit_in_context_appends_one_line_per_call(tmp_path: Path):
    """Multiple emits within one context = multiple lines in the same file."""
    with event_context(session_id="s", basedir=tmp_path):
        emit("first")
        emit("second")
        emit("third", count=42)
    diary = diary_path_for(tmp_path, "s")
    lines = diary.read_text().splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["event_type"] == "first"
    assert parsed[1]["event_type"] == "second"
    assert parsed[2]["event_type"] == "third"
    assert parsed[2]["count"] == 42


def test_emit_diary_record_has_all_fields(tmp_path: Path):
    """Every kwarg ends up as a JSON key alongside ts + event_type."""
    with event_context(session_id="s", basedir=tmp_path):
        emit("rich_event", provider="openrouter", status=429, model="some-model")
    [line] = diary_path_for(tmp_path, "s").read_text().splitlines()
    rec = json.loads(line)
    assert rec["provider"] == "openrouter"
    assert rec["status"] == 429
    assert rec["model"] == "some-model"


def test_emit_diary_handles_unicode_fields(tmp_path: Path):
    """ensure_ascii=False — unicode written as-is, not escaped."""
    with event_context(session_id="s", basedir=tmp_path):
        emit("unicode_event", text="café ☕ 日本語")
    [line] = diary_path_for(tmp_path, "s").read_text(encoding="utf-8").splitlines()
    rec = json.loads(line)
    assert rec["text"] == "café ☕ 日本語"


def test_emit_diary_uses_default_str_fallback_for_non_json_values(tmp_path: Path):
    """json.dumps(default=str) — Path objects etc. become their str repr."""
    with event_context(session_id="s", basedir=tmp_path):
        emit("path_event", where=Path("/some/path"))
    [line] = diary_path_for(tmp_path, "s").read_text().splitlines()
    rec = json.loads(line)
    assert rec["where"] == "/some/path"


# --- context isolation between sessions ------------------------------- #
def test_emit_routes_to_correct_session_diary(tmp_path: Path):
    """Two sequential contexts → two separate diary files."""
    with event_context(session_id="sess-A", basedir=tmp_path):
        emit("event_A")
    with event_context(session_id="sess-B", basedir=tmp_path):
        emit("event_B")
    diary_a = diary_path_for(tmp_path, "sess-A")
    diary_b = diary_path_for(tmp_path, "sess-B")
    assert "event_A" in diary_a.read_text()
    assert "event_B" in diary_b.read_text()
    assert "event_A" not in diary_b.read_text()
    assert "event_B" not in diary_a.read_text()


def test_event_context_resets_on_exit(tmp_path: Path):
    """After a context exits, emit() falls back to log-only."""
    with event_context(session_id="s", basedir=tmp_path):
        emit("inside")
    emit("outside")
    diary = diary_path_for(tmp_path, "s")
    # Only "inside" should be in the diary.
    text = diary.read_text()
    assert "inside" in text
    assert "outside" not in text


def test_event_context_nesting_restores_outer(tmp_path: Path):
    """Nested contexts restore the outer binding on inner exit."""
    with event_context(session_id="outer", basedir=tmp_path):
        emit("e_outer_1")
        with event_context(session_id="inner", basedir=tmp_path):
            emit("e_inner")
        # Back to outer context after inner exits.
        emit("e_outer_2")

    outer_diary = diary_path_for(tmp_path, "outer")
    inner_diary = diary_path_for(tmp_path, "inner")
    assert "e_outer_1" in outer_diary.read_text()
    assert "e_outer_2" in outer_diary.read_text()
    assert "e_inner" in inner_diary.read_text()
    assert "e_inner" not in outer_diary.read_text()


def test_event_context_thread_isolation(tmp_path: Path):
    """ContextVar semantics: each thread gets its own context.

    Without isolation, the second thread's emit could land in the
    first's diary. Python's ContextVars are thread-local-ish (each
    thread has its own copy of the context); this pins that.
    """
    results: dict[str, str] = {}

    def worker(session_id: str) -> None:
        with event_context(session_id=session_id, basedir=tmp_path):
            emit(f"event_{session_id}")
        results[session_id] = "done"

    t1 = threading.Thread(target=worker, args=("sess-T1",))
    t2 = threading.Thread(target=worker, args=("sess-T2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Each session's diary contains its own event only.
    text_t1 = diary_path_for(tmp_path, "sess-T1").read_text()
    text_t2 = diary_path_for(tmp_path, "sess-T2").read_text()
    assert "event_sess-T1" in text_t1
    assert "event_sess-T2" in text_t2
    assert "event_sess-T1" not in text_t2
    assert "event_sess-T2" not in text_t1


# --- fail-open on diary write errors ---------------------------------- #
def test_emit_swallows_diary_write_failure(tmp_path: Path, caplog, monkeypatch):
    """If the diary write raises, emit() logs WARNING and returns."""
    caplog.set_level(logging.WARNING, logger="memeval.dreaming.events")

    real_open = builtins.open

    def fail_on_diary_open(path, mode="r", *args, **kwargs):
        if "daydream-events.jsonl" in str(path) and ("a" in mode or "w" in mode):
            raise OSError("simulated disk-full")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fail_on_diary_open)

    with event_context(session_id="s", basedir=tmp_path):
        # Must not raise.
        emit("triggers_disk_full")

    # Failure was logged.
    assert any(
        "diary write failed" in r.getMessage() and "triggers_disk_full" in r.getMessage()
        for r in caplog.records
    )


# --- local-only invariant (ADR-011 mirror for diary file) ------------ #
def test_emit_in_context_makes_no_network_connect(tmp_path: Path, monkeypatch):
    def _no_connect(self, *args, **kwargs):
        raise AssertionError(f"network connect attempted by emit(): {args!r}")

    monkeypatch.setattr(socket.socket, "connect", _no_connect)
    with event_context(session_id="s", basedir=tmp_path):
        emit("local_only_event", count=1)


def test_diary_uses_append_mode(tmp_path: Path):
    """Diary is append-only — second emit() doesn't truncate the first."""
    with event_context(session_id="s", basedir=tmp_path):
        emit("first", payload="A")
    # Simulate a second invocation (new context, same session).
    with event_context(session_id="s", basedir=tmp_path):
        emit("second", payload="B")
    lines = diary_path_for(tmp_path, "s").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["payload"] == "A"
    assert json.loads(lines[1])["payload"] == "B"


# --- existing OpenRouterClient call sites still work ----------------- #
def test_pr2_call_sites_emit_without_context(caplog):
    """The LLMClient call sites (PR2) don't bind context — they should
    still log without error or diary churn. Mirrors how OpenRouterClient
    behaves when invoked outside an engine context (e.g., direct test
    usage)."""
    pytest.importorskip(
        "detect_secrets",
        reason="install with `pip install -e eval[daydream]` to run",
    )
    from memeval.dreaming.llm import OpenRouterClient
    from memeval.dreaming.redaction import RedactedText

    # No event_context bound. OpenRouterClient calls emit("llm_unavailable", ...).
    # Should log + not raise + not write a diary.
    caplog.set_level(logging.DEBUG, logger="memeval.dreaming.events")
    client = OpenRouterClient(api_key=None)  # explicitly unset
    client.complete(RedactedText("anything"))
    assert any("llm_unavailable" in r.getMessage() for r in caplog.records)


# --- DREAM_DEBUG=1 stdout opt-in (replay-script + local-dev surface) -------- #
def test_dream_debug_unset_no_stdout(monkeypatch, capsys):
    """Default — no env var — emit() never touches stdout."""
    monkeypatch.delenv("DREAM_DEBUG", raising=False)
    emit("silent_event", k="v")
    captured = capsys.readouterr()
    assert captured.out == "", f"unexpected stdout: {captured.out!r}"


def test_dream_debug_set_to_zero_no_stdout(monkeypatch, capsys):
    """Only the literal value '1' enables the mirror (not '0', 'true', etc.)."""
    monkeypatch.setenv("DREAM_DEBUG", "0")
    emit("still_silent", k="v")
    captured = capsys.readouterr()
    assert captured.out == ""


def test_dream_debug_emits_one_jsonl_line_per_event(monkeypatch, capsys):
    """DREAM_DEBUG=1 → one JSONL line per emit, parseable, carrying event_type + fields."""
    monkeypatch.setenv("DREAM_DEBUG", "1")
    emit("debug_event_a", a=1)
    emit("debug_event_b", b="x")
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2
    parsed = [json.loads(line) for line in out]
    assert parsed[0]["event_type"] == "debug_event_a"
    assert parsed[0]["a"] == 1
    assert "ts" in parsed[0]
    assert parsed[1]["event_type"] == "debug_event_b"
    assert parsed[1]["b"] == "x"


def test_dream_debug_fires_outside_event_context(monkeypatch, capsys, tmp_path):
    """Stdout mirror works even when no event_context is bound (diary skip path)."""
    monkeypatch.setenv("DREAM_DEBUG", "1")
    # NOT inside event_context — diary path is short-circuited but stdout still fires.
    emit("loose_event", n=7)
    out = capsys.readouterr().out
    assert out, "stdout mirror should fire even without context bound"
    record = json.loads(out)
    assert record["event_type"] == "loose_event"
    # No diary file created either.
    assert not any(tmp_path.rglob("*.jsonl"))


def test_dream_debug_does_not_replace_diary_when_context_bound(
    monkeypatch, capsys, tmp_path
):
    """DREAM_DEBUG=1 is ADDITIVE — diary still gets the record when context is bound."""
    monkeypatch.setenv("DREAM_DEBUG", "1")
    with event_context(session_id="sid-1", basedir=tmp_path):
        emit("dual_event", x=1)
    # Stdout mirror fired
    stdout_record = json.loads(capsys.readouterr().out)
    assert stdout_record["event_type"] == "dual_event"
    # Diary also written
    diary_lines = diary_path_for(tmp_path, "sid-1").read_text().splitlines()
    assert len(diary_lines) == 1
    diary_record = json.loads(diary_lines[0])
    assert diary_record["event_type"] == "dual_event"
    assert diary_record["x"] == 1


def test_dream_debug_stdout_failure_does_not_propagate(monkeypatch):
    """If stdout.write blows up, emit() swallows it (fail-open contract)."""
    monkeypatch.setenv("DREAM_DEBUG", "1")
    import sys

    def _explode(*a, **kw):
        raise IOError("broken pipe")

    monkeypatch.setattr(sys.stdout, "write", _explode)
    # Must not raise.
    emit("should_not_propagate", k="v")
