"""Tests for the Stop/PreCompact subprocess wiring in hooks_handler.

Covers MIGRATION_STOP_HOOK_RUBRIC.md §A (subprocess shape on Stop/PreCompact),
§B (fail-open contract), §C (non-gated regression guard), §D (event gating),
§F (selective env passthrough + FileNotFoundError stderr), §I (integration).

All tests monkeypatch `subprocess.run` — they do NOT require the daydream console
script to be installed.
"""

from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cookbook_memory.adapters.claude_code import hooks_handler


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class _Recorder:
    """Records subprocess.run calls; returns a fake CompletedProcess by default."""

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self._raises = raises

    def __call__(self, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append((args, kwargs))
        if self._raises is not None:
            raise self._raises
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")


def _patch_subprocess(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    monkeypatch.setattr(hooks_handler.subprocess, "run", recorder)


def _make_settings_via_env(monkeypatch: pytest.MonkeyPatch, store_dir: Path) -> None:
    """Convenience: ensure MEMORY_STORE is set so Settings.from_env populates store_path."""
    monkeypatch.setenv("MEMORY_STORE", str(store_dir))


# --------------------------------------------------------------------------- #
# §A — subprocess wiring on Stop / PreCompact
# --------------------------------------------------------------------------- #


def test_handle_imports_subprocess_and_os_at_module_top() -> None:
    """§A criteria 1, 2 — `subprocess` AND `os` are top-level imports."""
    src = Path(hooks_handler.__file__).read_text()
    tree = ast.parse(src)
    top_imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_imports.add(alias.name)
    assert "subprocess" in top_imports
    assert "os" in top_imports


def test_handle_calls_subprocess_run_once_on_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§A criterion 4 — Stop fires subprocess.run exactly once."""
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("Stop", {"session_id": "s1"})
    assert len(recorder.calls) == 1


def test_subprocess_call_uses_hook_interpreter_module_form(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§A criterion 5 — subprocess runs daydream through this hook's interpreter."""
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("Stop", {"session_id": "s1"})
    args, _kwargs = recorder.calls[0]
    assert args[0] == [sys.executable, "-m", "memeval.dreaming.cli", "daydream"]


def test_subprocess_call_does_not_use_shell(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§A criterion 6 — shell=False (or omitted)."""
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("Stop", {"session_id": "s1"})
    _args, kwargs = recorder.calls[0]
    assert kwargs.get("shell", False) is False


def test_subprocess_input_is_verbatim_json_dumps_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§A criterion 7 — input kwarg equals json.dumps(payload) verbatim."""
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    payload = {"session_id": "s1", "transcript_path": "/tmp/log", "hook_event_name": "Stop"}
    hooks_handler.handle("Stop", payload)
    _args, kwargs = recorder.calls[0]
    assert kwargs["input"] == json.dumps(payload)


def test_subprocess_env_injects_memory_store_when_settings_has_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§A criterion 9 — env carries MEMORY_STORE=str(settings.store_path) when set."""
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("Stop", {"session_id": "s1"})
    _args, kwargs = recorder.calls[0]
    assert kwargs["env"]["MEMORY_STORE"] == str(tmp_path)


def test_subprocess_env_omits_memory_store_when_settings_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """§A criterion 10 — when MEMORY_STORE is unset and no store override, env omits it."""
    monkeypatch.delenv("MEMORY_STORE", raising=False)
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    hooks_handler.handle("Stop", {"session_id": "s1"})
    _args, kwargs = recorder.calls[0]
    assert "MEMORY_STORE" not in kwargs["env"]


def test_subprocess_call_has_positive_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§A criterion 11 — timeout is a positive int."""
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("Stop", {"session_id": "s1"})
    _args, kwargs = recorder.calls[0]
    assert isinstance(kwargs["timeout"], int)
    assert kwargs["timeout"] > 0


def test_subprocess_timeout_is_600s_on_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§A criterion 12 — Stop timeout is exactly 600s."""
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("Stop", {"session_id": "s1"})
    _args, kwargs = recorder.calls[0]
    assert kwargs["timeout"] == 600


def test_subprocess_timeout_is_120s_on_precompact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§A criterion 12b — PreCompact timeout is exactly 120s (sync; shorter ceiling — halliday F11)."""
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("PreCompact", {"session_id": "s1"})
    _args, kwargs = recorder.calls[0]
    assert kwargs["timeout"] == 120


def test_no_shell_true_in_hooks_handler() -> None:
    """§A criterion 13 — source contains zero `shell=True`."""
    src = Path(hooks_handler.__file__).read_text()
    assert "shell=True" not in src


# --------------------------------------------------------------------------- #
# §B — subprocess fail-open contract
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("exc", [
    subprocess.TimeoutExpired(cmd=[sys.executable, "-m", "memeval.dreaming.cli", "daydream"], timeout=600),
    subprocess.CalledProcessError(returncode=1, cmd=[sys.executable, "-m", "memeval.dreaming.cli", "daydream"]),
    RuntimeError("boom"),
])
def test_handle_failopens_on_exception(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, exc: BaseException) -> None:
    """§B criteria 15, 17, 18 — every exception class → handle() returns {}."""
    recorder = _Recorder(raises=exc)
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    assert hooks_handler.handle("Stop", {"session_id": "s1"}) == {}


def test_handle_failopens_on_filenotfounderror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§B criterion 16 — FileNotFoundError launching the daydream module → handle() returns {}."""
    recorder = _Recorder(raises=FileNotFoundError(2, "No such file or directory"))
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    assert hooks_handler.handle("Stop", {"session_id": "s1"}) == {}


def test_handle_does_not_swallow_keyboardinterrupt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§B criterion 19 — KeyboardInterrupt propagates."""
    recorder = _Recorder(raises=KeyboardInterrupt())
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    with pytest.raises(KeyboardInterrupt):
        hooks_handler.handle("Stop", {"session_id": "s1"})


# --------------------------------------------------------------------------- #
# §C — non-Stop/PreCompact regression guard
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("event_name", ["SessionStart", "UserPromptSubmit", "PostCompact"])
def test_handle_does_not_spawn_subprocess_on_non_gated_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, event_name: str
) -> None:
    """§C criteria 20–22 — non-gated events never spawn a subprocess."""
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle(event_name, {"session_id": "s1"})
    assert len(recorder.calls) == 0


def test_handle_still_emits_note_event_on_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§C criterion 23 — Stop still emits the `note` event (regression guard)."""
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("Stop", {"session_id": "s1"})
    events_lines = (tmp_path / "events.jsonl").read_text().splitlines()
    parsed = [json.loads(line) for line in events_lines if line.strip()]
    assert any(e["op"] == "note" for e in parsed)


# --------------------------------------------------------------------------- #
# §D — subprocess-fire / failure events
# --------------------------------------------------------------------------- #


def test_handle_emits_hook_subprocess_fired_event_on_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§D criterion 24 — successful subprocess.run on Stop emits `daydream.hook_subprocess_fired` once."""
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("Stop", {"session_id": "s1"})
    events_lines = (tmp_path / "events.jsonl").read_text().splitlines()
    parsed = [json.loads(line) for line in events_lines if line.strip()]
    fired = [e for e in parsed if e["op"] == "daydream.hook_subprocess_fired"]
    assert len(fired) == 1


def test_fired_event_records_child_returncode_and_output_tails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Successful child launch records whether the daydream CLI actually complained."""

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=7,
            stdout="out-detail",
            stderr="err-detail",
        )

    monkeypatch.setattr(hooks_handler.subprocess, "run", fake_run)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle(
        "Stop",
        {"session_id": "s1", "transcript_path": str(tmp_path / "missing.jsonl")},
    )
    parsed = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    fired = [e for e in parsed if e["op"] == "daydream.hook_subprocess_fired"]
    assert fired[0]["meta"]["returncode"] == 7
    assert fired[0]["meta"]["stdout_tail"] == "out-detail"
    assert fired[0]["meta"]["stderr_tail"] == "err-detail"


def test_fired_event_records_payload_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The parent hook logs the payload facts needed to debug daydream no-ops."""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}", encoding="utf-8")
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("Stop", {"session_id": "s1", "transcript_path": str(transcript)})
    parsed = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    fired = [e for e in parsed if e["op"] == "daydream.hook_subprocess_fired"]
    assert fired[0]["meta"]["has_session_id"] is True
    assert fired[0]["meta"]["has_transcript_path"] is True
    assert fired[0]["meta"]["transcript_path"] == str(transcript)
    assert fired[0]["meta"]["transcript_exists"] is True
    assert fired[0]["meta"]["payload_keys"] == ["session_id", "transcript_path"]


def test_incomplete_payload_event_emitted_when_transcript_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Missing transcript_path is explicit, not hidden behind a generic fired event."""
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("Stop", {"session_id": "s1"})
    parsed = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    incomplete = [e for e in parsed if e["op"] == "daydream.hook_payload_incomplete"]
    assert len(incomplete) == 1
    assert incomplete[0]["meta"]["has_session_id"] is True
    assert incomplete[0]["meta"]["has_transcript_path"] is False
    assert incomplete[0]["meta"]["payload_keys"] == ["session_id"]


def test_handle_emits_hook_subprocess_failed_event_on_exception(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§D criterion 25 — caught exception → `daydream.hook_subprocess_failed` event."""
    recorder = _Recorder(raises=RuntimeError("boom"))
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("Stop", {"session_id": "s1"})
    events_lines = (tmp_path / "events.jsonl").read_text().splitlines()
    parsed = [json.loads(line) for line in events_lines if line.strip()]
    failed = [e for e in parsed if e["op"] == "daydream.hook_subprocess_failed"]
    assert len(failed) == 1


def test_failed_event_records_exception_class_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§D criterion 26 — failed event's payload includes the exception class name."""
    recorder = _Recorder(raises=RuntimeError("boom"))
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("Stop", {"session_id": "s1"})
    events_lines = (tmp_path / "events.jsonl").read_text().splitlines()
    parsed = [json.loads(line) for line in events_lines if line.strip()]
    failed = [e for e in parsed if e["op"] == "daydream.hook_subprocess_failed"]
    assert failed
    serialized = json.dumps(failed[0])
    assert "RuntimeError" in serialized


def test_fired_event_not_emitted_on_non_gated_events(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§D criterion 27 — non-gated events do not emit `daydream.hook_subprocess_fired`."""
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("SessionStart", {"session_id": "s1"})
    events_lines = (tmp_path / "events.jsonl").read_text().splitlines()
    parsed = [json.loads(line) for line in events_lines if line.strip()]
    fired = [e for e in parsed if e["op"] == "daydream.hook_subprocess_fired"]
    assert len(fired) == 0


# --------------------------------------------------------------------------- #
# §F — selective env passthrough + FileNotFoundError stderr
# --------------------------------------------------------------------------- #


def test_subprocess_env_is_minimum_surface(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§F criterion 36 — env keys are a subset of the allowlist (no os.environ superset)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sentinel-secret")
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("Stop", {"session_id": "s1"})
    _args, kwargs = recorder.calls[0]
    assert set(kwargs["env"].keys()).issubset(hooks_handler._ALLOWED_ENV_KEYS)


def test_subprocess_env_passes_openrouter_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§F criterion 37 — OPENROUTER_API_KEY flows through when set in parent."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-xyz")
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("Stop", {"session_id": "s1"})
    _args, kwargs = recorder.calls[0]
    assert kwargs["env"]["OPENROUTER_API_KEY"] == "test-key-xyz"


def test_subprocess_env_drops_unknown_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§F criterion 38 — ANTHROPIC_API_KEY / other non-allowlisted vars are dropped."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-1")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret-2")
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("Stop", {"session_id": "s1"})
    _args, kwargs = recorder.calls[0]
    assert "ANTHROPIC_API_KEY" not in kwargs["env"]
    assert "AWS_SECRET_ACCESS_KEY" not in kwargs["env"]


def test_handle_writes_filenotfounderror_message_to_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """§F criterion 39 — FileNotFoundError → stderr line names the daydream module."""
    recorder = _Recorder(raises=FileNotFoundError(2, "No such file"))
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    hooks_handler.handle("Stop", {"session_id": "s1"})
    captured = capsys.readouterr()
    assert "memeval.dreaming.cli" in captured.err


# --------------------------------------------------------------------------- #
# §I — integration: main() drives the wiring
# --------------------------------------------------------------------------- #


def test_main_stop_invokes_subprocess(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§I criteria 50, 51 — main(["Stop"]) reads stdin JSON + invokes the daydream module."""
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    payload = {"session_id": "smk", "transcript_path": "/tmp/x", "hook_event_name": "Stop"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hooks_handler.main(["Stop"]) == 0
    assert len(recorder.calls) == 1
    args, _kwargs = recorder.calls[0]
    assert args[0] == [sys.executable, "-m", "memeval.dreaming.cli", "daydream"]


def test_main_stop_subprocess_input_is_verbatim_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§I criterion 52 — main()'s subprocess input is json.dumps(payload) of the stdin payload."""
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    payload = {"session_id": "smk", "transcript_path": "/tmp/x", "hook_event_name": "Stop"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    hooks_handler.main(["Stop"])
    _args, kwargs = recorder.calls[0]
    assert kwargs["input"] == json.dumps(payload)


def test_main_sessionstart_does_not_invoke_subprocess(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """§I criterion 53 — main(["SessionStart"]) with a non-empty payload does NOT spawn a subprocess."""
    recorder = _Recorder()
    _patch_subprocess(monkeypatch, recorder)
    _make_settings_via_env(monkeypatch, tmp_path)
    payload = {"session_id": "smk", "hook_event_name": "SessionStart"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hooks_handler.main(["SessionStart"]) == 0
    assert len(recorder.calls) == 0


# --------------------------------------------------------------------------- #
# Option B: plugin-side recall injection ($MEMORY_INJECT_RECALL)
# --------------------------------------------------------------------------- #


class _FakeHit:
    def __init__(self, content: str) -> None:
        self.content = content


def _patch_recall(monkeypatch: pytest.MonkeyPatch, hits: list) -> None:
    """Patch MemoryClient so _recall_injection returns `hits` without a real store."""
    import cookbook_memory.core.client as client_mod

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None: ...
        def recall(self, query: str, k: int = 5) -> list:
            return hits

    monkeypatch.setattr(client_mod, "MemoryClient", _FakeClient)


def test_inject_off_by_default_returns_empty(monkeypatch, tmp_path) -> None:
    """Unset toggle -> no injection, byte-identical to the historical default."""
    monkeypatch.delenv("MEMORY_INJECT_RECALL", raising=False)
    _patch_recall(monkeypatch, [_FakeHit("should not appear")])
    _make_settings_via_env(monkeypatch, tmp_path)
    assert hooks_handler.handle("UserPromptSubmit", {"session_id": "s", "prompt": "fix it"}) == {}


def test_inject_on_injects_recalled_memories(monkeypatch, tmp_path) -> None:
    """Toggle on + UserPromptSubmit + hits -> additionalContext with the memories."""
    monkeypatch.setenv("MEMORY_INJECT_RECALL", "1")
    _patch_recall(monkeypatch, [_FakeHit("use _replace, not __init__"), _FakeHit("run the io tests")])
    _make_settings_via_env(monkeypatch, tmp_path)
    out = hooks_handler.handle("UserPromptSubmit", {"session_id": "s", "prompt": "fix IndexVariable"})
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "use _replace, not __init__" in ctx and "run the io tests" in ctx


def test_inject_only_on_userpromptsubmit(monkeypatch, tmp_path) -> None:
    """The toggle never injects on non-UserPromptSubmit events."""
    monkeypatch.setenv("MEMORY_INJECT_RECALL", "1")
    _patch_recall(monkeypatch, [_FakeHit("nope")])
    _make_settings_via_env(monkeypatch, tmp_path)
    assert hooks_handler.handle("SessionStart", {"session_id": "s", "prompt": "x"}) == {}


def test_inject_failopen_on_no_hits_and_errors(monkeypatch, tmp_path) -> None:
    """No hits -> {}; a recall that raises -> {} (never breaks the turn)."""
    monkeypatch.setenv("MEMORY_INJECT_RECALL", "1")
    _make_settings_via_env(monkeypatch, tmp_path)
    _patch_recall(monkeypatch, [])  # no hits
    assert hooks_handler.handle("UserPromptSubmit", {"session_id": "s", "prompt": "x"}) == {}

    import cookbook_memory.core.client as client_mod

    class _Boom:
        def __init__(self, **kwargs: Any) -> None: ...
        def recall(self, *a: Any, **k: Any) -> list:
            raise RuntimeError("store exploded")

    monkeypatch.setattr(client_mod, "MemoryClient", _Boom)
    assert hooks_handler.handle("UserPromptSubmit", {"session_id": "s", "prompt": "x"}) == {}


def test_inject_empty_prompt_no_injection(monkeypatch, tmp_path) -> None:
    """A blank prompt -> no recall, no injection."""
    monkeypatch.setenv("MEMORY_INJECT_RECALL", "1")
    _patch_recall(monkeypatch, [_FakeHit("nope")])
    _make_settings_via_env(monkeypatch, tmp_path)
    assert hooks_handler.handle("UserPromptSubmit", {"session_id": "s", "prompt": "   "}) == {}


def test_clean_query_strips_prefix_and_caps(monkeypatch) -> None:
    """The injection query is the short, prefix-free issue head — not the full
    multi-KB prompt (boilerplate prefix + issue + code)."""
    monkeypatch.delenv("MEMORY_INJECT_RECALL_QUERY_MAX", raising=False)
    natural = (
        "Persistent memory is available through recall if prior fixes would help. "
        "Edit the source files in this checkout directly to fix the issue, then run "
        "the tests to confirm. Do NOT output a diff or paste a patch — just make the "
        "edits.\n\n"
        "unicode dtype copy regression IndexVariable\n\n" + ("x" * 2000)
    )
    out = hooks_handler._clean_query(natural)
    assert not out.lower().startswith("persistent memory")        # prefix stripped
    assert out.startswith("unicode dtype copy regression IndexVariable")
    assert len(out) <= 320                                        # capped
    # no-prefix input passes through (still capped); fail-safe never empties non-empty
    assert hooks_handler._clean_query("fix the groupby attrs bug") == "fix the groupby attrs bug"
    assert hooks_handler._clean_query("") == ""
    # env override of the cap
    monkeypatch.setenv("MEMORY_INJECT_RECALL_QUERY_MAX", "60")
    assert len(hooks_handler._clean_query(natural)) <= 60
