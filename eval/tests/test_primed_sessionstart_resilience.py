"""Offline regression tests for the intermittent ``claude (primed) exited 1:
{"type":"system","subtype":"hook_started",...,"hook_event":"SessionStart"}`` failure
seen in plugin-real SWE-Bench-CL runs.

The harness-side fix is covered, with no real ``claude`` / no network:

**Diagnostics + bounded retry** (``cli._diagnose_primed_failure`` /
``cli.run_claude_primed``). The old error kept only the first 400 chars of stdout,
which on a startup abort is the ``SessionStart`` ``hook_started`` system event —
making every such failure LOOK like a hook bug when the hook returns 0 and claude
actually died mid-startup. We now (a) classify a "startup abort" (a ``system`` /
``hook_started`` event with NO ``result`` event) as transient and retry it once,
and (b) build the error message from stderr + the TAIL of stdout, not the head.
A genuine model error (which DOES emit a ``result`` event) is NOT retried.

(The cross-task scratch-hygiene fix originally bundled here is obsolete: ADR-harness-012
removed all harness-side store copy/prune — the plugin owns its store, so there is no
copy in which scratch could propagate. Those tests were dropped with the machinery.)

Run under the swebench venv with PYTHONPATH=. from eval/.
"""

from __future__ import annotations

import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
_BASE_DIR = _THIS.parent.parent
import sys

if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from memeval.claudecode import cli as C  # noqa: E402

SkipTest = unittest.SkipTest

# A minimal stream-json startup-abort stdout: the SessionStart hook fired, then claude
# exited with NO result event (the real-run signature, truncated for the test).
_STARTUP_ABORT_STDOUT = (
    '{"type":"system","subtype":"hook_started","hook_id":"5a6668b8",'
    '"hook_name":"SessionStart:startup","hook_event":"SessionStart","uuid":"8cff2049"}\n'
)
# A genuine model/tool error: a result event with is_error -> NOT a startup abort.
_MODEL_ERROR_STDOUT = (
    '{"type":"system","subtype":"init"}\n'
    '{"type":"result","subtype":"error_during_execution","is_error":true,'
    '"result":"the model produced an invalid tool call"}\n'
)


# --------------------------------------------------------------------------- #
# (1) Diagnostics: classification + a useful message.
# --------------------------------------------------------------------------- #
def test_startup_abort_is_classified_transient() -> None:
    diag = C._diagnose_primed_failure(_STARTUP_ABORT_STDOUT, "")
    assert diag.is_startup_abort is True
    assert diag.is_mcp_config_miss is False


def test_model_error_is_not_a_startup_abort() -> None:
    # A result event present -> a real error, must NOT be retried as a transient.
    diag = C._diagnose_primed_failure(_MODEL_ERROR_STDOUT, "")
    assert diag.is_startup_abort is False


def test_diagnosis_message_prefers_stderr_over_stdout_head() -> None:
    # The real crash reason is on stderr; stdout's head is the misleading hook event.
    diag = C._diagnose_primed_failure(
        _STARTUP_ABORT_STDOUT, "Traceback: RuntimeError: real startup crash")
    assert "real startup crash" in diag.message
    # The misleading hook event must NOT be what we surface when stderr exists.
    assert "hook_started" not in diag.message


def test_mcp_config_miss_still_detected() -> None:
    diag = C._diagnose_primed_failure("", "Error: MCP config file not found")
    assert diag.is_mcp_config_miss is True


# --------------------------------------------------------------------------- #
# (1) Bounded retry: a transient startup abort is retried, a model error is not.
# --------------------------------------------------------------------------- #
class _FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_subprocess_run(monkeypatch_target, responses: list) -> list:
    """Replace cli.subprocess.run with a stub that yields ``responses`` in order and
    records each call. Returns the call-record list."""
    calls: list = []
    it = iter(responses)

    def fake_run(argv, **kw):  # noqa: ANN001
        calls.append(argv)
        return next(it)

    monkeypatch_target.subprocess.run = fake_run  # type: ignore[attr-defined]
    return calls


def _with_native_runtime():
    from memeval.claudecode.platform import ClaudeRuntime
    return ClaudeRuntime(kind="native", exe="claude", python="python")


def test_run_claude_primed_retries_startup_abort_then_succeeds() -> None:
    rt = _with_native_runtime()
    ok_stdout = '{"type":"result","subtype":"success","result":"answer"}\n'
    responses = [
        _FakeProc(1, stdout=_STARTUP_ABORT_STDOUT),   # transient startup abort
        _FakeProc(0, stdout=ok_stdout),               # retry succeeds
    ]
    import memeval.claudecode.cli as cli_mod
    orig = cli_mod.subprocess.run
    try:
        calls = _patch_subprocess_run(cli_mod, responses)
        res = cli_mod.run_claude_primed("q", cwd=".", runtime=rt)
        assert res.text == "answer"
        assert len(calls) == 2          # it retried exactly once
    finally:
        cli_mod.subprocess.run = orig


def test_run_claude_primed_does_not_retry_model_error() -> None:
    rt = _with_native_runtime()
    responses = [_FakeProc(1, stdout=_MODEL_ERROR_STDOUT)]  # only one; a retry would StopIteration
    import memeval.claudecode.cli as cli_mod
    orig = cli_mod.subprocess.run
    try:
        calls = _patch_subprocess_run(cli_mod, responses)
        raised = False
        try:
            cli_mod.run_claude_primed("q", cwd=".", runtime=rt)
        except RuntimeError as exc:
            raised = True
            assert "invalid tool call" in str(exc)   # the real reason, surfaced
        assert raised
        assert len(calls) == 1          # NOT retried (a real model error)
    finally:
        cli_mod.subprocess.run = orig


# --------------------------------------------------------------------------- #
# Built-in runner (no pytest required).
# --------------------------------------------------------------------------- #
def _all_tests() -> list:
    g = globals()
    names = [n for n in g if n.startswith("test_") and callable(g[n])]
    names.sort(key=lambda n: g[n].__code__.co_firstlineno)
    return [(n, g[n]) for n in names]


def main() -> int:
    passed = failed = skipped = 0
    for name, fn in _all_tests():
        try:
            fn()
            passed += 1
            print(f"PASS {name}")
        except SkipTest as exc:
            skipped += 1
            print(f"SKIP {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"FAIL {name}: {exc}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
