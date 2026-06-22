"""Offline end-to-end tests for the agentic CODE path + LocalExecGrader.

These prove the whole loop — `prepare_checkout` -> claude edits the checkout ->
`capture_diff` (the prediction) -> `LocalExecGrader` (apply patch + gold tests +
run) — runs DETERMINISTICALLY with **no network, no real `claude`, no container
runtime, no real git/venv**. The fixture `swe_contextbench.json` drives the
*task shape*; the checkout itself is synthesized by an injected fake git runner
(the fixture is not a real repo). A real swe_contextbench run additionally needs
network (clone by SHA), live `claude` subscription auth, and a buildable repo —
none available offline; that is the intentional limit of this suite (see
ADR-eval-003).

Run under the Py313 interpreter with PYTHONIOENCODING=utf-8.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Optional

# Make the package importable when run as `python tests/test_claudecode_code_agent.py`.
_THIS = Path(__file__).resolve()
_TESTS_DIR = _THIS.parent
_BASE_DIR = _TESTS_DIR.parent
_FIXTURES = _TESTS_DIR / "fixtures"

import sys

if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from memeval.schema import Benchmark  # noqa: E402
from memeval.agent import run_agent  # noqa: E402
from memeval.claudecode.agent import ClaudeCodeAgent, _build_code_agent_prompt  # noqa: E402
from memeval.claudecode.cli import ClaudeResult  # noqa: E402
from memeval.claudecode.checkout import (  # noqa: E402
    GitResult,
    capture_diff,
    prepare_checkout,
)
from memeval.claudecode.platform import ClaudeRuntime  # noqa: E402
from memeval import grader as G  # noqa: E402

SkipTest = unittest.SkipTest


def _fixture(name: str) -> str:
    return str(_FIXTURES / name)


# --------------------------------------------------------------------------- #
# Fakes: a stub git runner + a fake claude runner. No network, no real git.
# --------------------------------------------------------------------------- #
# The "fixed" diff the fake git runner reports from `capture_diff` once the fake
# claude has "edited" the checkout — a clean unified diff anchored at diff --git.
_FIXED_DIFF = (
    "diff --git a/orm.py b/orm.py\n"
    "--- a/orm.py\n"
    "+++ b/orm.py\n"
    "@@ -1 +1 @@\n"
    "-    return None\n"
    "+    return []\n"
)


def _make_fake_git(*, diff: str = _FIXED_DIFF, apply_ok: bool = True,
                   edited_flag: Optional[dict] = None):
    """A fake GitRunner that synthesizes a checkout, a diff, and patch application
    entirely on disk — no real git, no network. ``edited_flag`` (a dict) is set
    True once the working tree has been "edited" so `capture_diff` only reports a
    non-empty diff after an edit happened."""

    def _fake_git(args, cwd, *a, **kw) -> GitResult:
        cwd = Path(cwd)
        op = args[0] if args else ""
        if op in ("init", "remote", "fetch", "checkout", "clone"):
            # Materialize the base checkout: a broken orm.py + its tests.
            cwd.mkdir(parents=True, exist_ok=True)
            (cwd / "orm.py").write_text("def filter_empty():\n    return None\n",
                                        encoding="utf-8")
            return GitResult(returncode=0)
        if op == "add":
            return GitResult(returncode=0)
        if op == "diff":
            # Report the fix diff only when the tree was edited (a real agent edit).
            if edited_flag is None or edited_flag.get("edited"):
                return GitResult(returncode=0, stdout=diff)
            return GitResult(returncode=0, stdout="")
        if op == "apply":
            return GitResult(returncode=0 if apply_ok else 1,
                             stderr="" if apply_ok else "patch does not apply")
        return GitResult(returncode=0)

    return _fake_git


def _make_fake_claude(*, edited_flag: dict, edit: bool = True):
    """A fake claude runner that simulates the agent editing files in the checkout
    (writing the fixed orm.py) and flips ``edited_flag``. Asserts the agentic
    invariants: acceptEdits/bypass permission + full toolset (allowed_tools None)."""

    def _fake_claude(prompt, *, cwd, permission_mode="bypassPermissions",
                     allowed_tools=None, append_system_prompt=None, **kw) -> ClaudeResult:
        assert permission_mode in ("acceptEdits", "bypassPermissions"), permission_mode
        assert allowed_tools is None, "agentic CODE must use the full native toolset"
        if edit:
            (Path(cwd) / "orm.py").write_text("def filter_empty():\n    return []\n",
                                              encoding="utf-8")
            edited_flag["edited"] = True
        return ClaudeResult(text="done", tokens_in=20, tokens_out=4)

    return _fake_claude


def _make_fake_cmd(*, fail_to_pass_passed=True, pass_to_pass_passed=True,
                   raise_on_pytest=False):
    """A fake CmdRunner returning canned pytest output for the named selectors."""

    def _fake_cmd(args, cwd, env=None, *a, **kw):
        from memeval.grader import CmdResult

        if args and args[-1].endswith(".") and "install" in args:
            return CmdResult(returncode=0)  # editable install "succeeds"
        if "pytest" in args:
            if raise_on_pytest:
                raise RuntimeError("env build / pytest invocation failed")
            lines = []
            # selectors are the trailing args after `-q`
            for sel in args[args.index("-q") + 1:]:
                if "empty" in sel or "index" in sel:
                    lines.append(f"{sel} {'PASSED' if fail_to_pass_passed else 'FAILED'}")
                else:
                    lines.append(f"{sel} {'PASSED' if pass_to_pass_passed else 'FAILED'}")
            rc = 0 if (fail_to_pass_passed and pass_to_pass_passed) else 1
            return CmdResult(returncode=rc, stdout="\n".join(lines) + "\n")
        return CmdResult(returncode=0)

    return _fake_cmd


_NATIVE = ClaudeRuntime(kind="native", exe="claude", python="python")


# --------------------------------------------------------------------------- #
# checkout.py unit coverage
# --------------------------------------------------------------------------- #
def test_prepare_checkout_and_capture_diff_with_fake_git() -> None:
    flag: dict = {}
    git = _make_fake_git(edited_flag=flag)
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "repo"
        out = prepare_checkout("example/django-fork", "aaaa1111", dest, git_runner=git)
        assert out == dest.resolve()
        assert (dest / "orm.py").exists()              # checkout materialized
        # No edit yet -> empty diff (honest empty patch).
        assert capture_diff(dest, git_runner=git) == ""
        # After an "edit", capture_diff reports the fix.
        flag["edited"] = True
        diff = capture_diff(dest, git_runner=git)
        assert diff.startswith("diff --git a/orm.py b/orm.py")


# --------------------------------------------------------------------------- #
# Test A — agentic solve records a generate step + captured diff prediction
# --------------------------------------------------------------------------- #
def test_agentic_solve_produces_diff_and_records_generate() -> None:
    flag: dict = {}
    git = _make_fake_git(edited_flag=flag)
    claude = _make_fake_claude(edited_flag=flag)
    # A grader that just confirms the prediction reached it and is non-empty.
    seen: dict = {}

    def _grader(task, prediction):
        seen["prediction"] = prediction
        return None  # ungraded here; Test C exercises the real LocalExecGrader

    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="off", code_mode="agentic",
                                runner=claude, git_runner=git, runtime=_NATIVE,
                                workdir=tmp)
        rr = run_agent(Benchmark.SWE_CONTEXTBENCH, agent, memory=False,
                       path_or_id=_fixture("swe_contextbench.json"), limit=1,
                       seed_sessions=False, grader=_grader)
    assert rr.n_tasks == 1
    kinds = [s.kind for t in rr.trajectories for s in t.steps]
    assert "generate" in kinds                                  # generate recorded
    pred = rr.trajectories[0].prediction
    assert pred.startswith("diff --git a/orm.py b/orm.py")      # captured git diff
    assert seen["prediction"] == pred                           # reached the grader
    # The agent NEVER self-grades: success comes from the grader (None here).
    assert rr.trajectories[0].success is None


def test_build_code_agent_prompt_says_edit_not_diff() -> None:
    from memeval.schema import Task, TaskKind

    t = Task(task_id="c", benchmark=Benchmark.SWE_CONTEXTBENCH, kind=TaskKind.CODE,
             question="Fix the crash.", repo="example/repo", base_commit="dead")
    p = _build_code_agent_prompt(t)
    assert "Fix the crash." in p
    assert "Repository: example/repo" in p and "Base commit: dead" in p
    assert "Edit the source files" in p
    assert "do NOT output a diff" in p.lower() or "do not output a diff" in p.lower()


# --------------------------------------------------------------------------- #
# Test B — LocalExecGrader over the stub repo with an injected command runner
# --------------------------------------------------------------------------- #
def _code_task(*, fail=("test_orm.py::test_empty",),
               pass_=("test_orm.py::test_basic",), test_patch="t"):
    from memeval.schema import Task, TaskKind

    return Task(
        task_id="scb_django_1", benchmark=Benchmark.SWE_CONTEXTBENCH, kind=TaskKind.CODE,
        question="fix", repo="example/django-fork", base_commit="aaaa1111",
        patch="diff", test_patch=test_patch,
        fail_to_pass=list(fail), pass_to_pass=list(pass_),
        metadata={"instance_id": "scb_django_1"},
    )


def test_local_exec_grader_resolved_when_all_pass() -> None:
    git = _make_fake_git()
    cmd = _make_fake_cmd(fail_to_pass_passed=True, pass_to_pass_passed=True)
    g = G.LocalExecGrader(runner=cmd, git_runner=git)
    assert g(_code_task(), _FIXED_DIFF) is True


def test_local_exec_grader_false_when_fail_to_pass_fails() -> None:
    git = _make_fake_git()
    cmd = _make_fake_cmd(fail_to_pass_passed=False, pass_to_pass_passed=True)
    g = G.LocalExecGrader(runner=cmd, git_runner=git)
    assert g(_code_task(), _FIXED_DIFF) is False


def test_local_exec_grader_false_on_pass_to_pass_regression() -> None:
    git = _make_fake_git()
    cmd = _make_fake_cmd(fail_to_pass_passed=True, pass_to_pass_passed=False)
    g = G.LocalExecGrader(runner=cmd, git_runner=git)
    assert g(_code_task(), _FIXED_DIFF) is False


def test_local_exec_grader_none_when_env_build_fails() -> None:
    # The honesty rule: any failure to build the env / run tests -> None (UNGRADED),
    # never a fake False.
    git = _make_fake_git()
    cmd = _make_fake_cmd(raise_on_pytest=True)
    g = G.LocalExecGrader(runner=cmd, git_runner=git)
    assert g(_code_task(), _FIXED_DIFF) is None


def test_local_exec_grader_empty_prediction_is_false() -> None:
    g = G.LocalExecGrader(runner=_make_fake_cmd(), git_runner=_make_fake_git())
    assert g(_code_task(), "") is False
    assert g(_code_task(), "   \n") is False


def test_local_exec_grader_none_when_patch_does_not_apply() -> None:
    git = _make_fake_git(apply_ok=False)   # `git apply` returns nonzero
    cmd = _make_fake_cmd()
    g = G.LocalExecGrader(runner=cmd, git_runner=git)
    assert g(_code_task(), _FIXED_DIFF) is None


# --------------------------------------------------------------------------- #
# Test C — the full loop wired together (deterministic accuracy)
# --------------------------------------------------------------------------- #
def test_full_agentic_loop_accuracy_one() -> None:
    flag: dict = {}
    git = _make_fake_git(edited_flag=flag)
    claude = _make_fake_claude(edited_flag=flag)
    grader = G.LocalExecGrader(runner=_make_fake_cmd(), git_runner=git)
    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="off", code_mode="agentic",
                                runner=claude, git_runner=git, runtime=_NATIVE,
                                workdir=tmp)
        rr = run_agent(Benchmark.SWE_CONTEXTBENCH, agent, memory=False,
                       path_or_id=_fixture("swe_contextbench.json"), limit=1,
                       seed_sessions=False, grader=grader)
    assert rr.n_tasks == 1
    assert rr.trajectories[0].success is True
    assert rr.metrics.accuracy == 1.0


def test_full_agentic_loop_noop_agent_accuracy_zero() -> None:
    # A no-op agent that writes nothing -> empty diff -> grader False -> accuracy 0.
    flag: dict = {}
    git = _make_fake_git(edited_flag=flag)
    claude = _make_fake_claude(edited_flag=flag, edit=False)   # no edit
    grader = G.LocalExecGrader(runner=_make_fake_cmd(), git_runner=git)
    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="off", code_mode="agentic",
                                runner=claude, git_runner=git, runtime=_NATIVE,
                                workdir=tmp)
        rr = run_agent(Benchmark.SWE_CONTEXTBENCH, agent, memory=False,
                       path_or_id=_fixture("swe_contextbench.json"), limit=1,
                       seed_sessions=False, grader=grader)
    assert rr.n_tasks == 1
    assert rr.trajectories[0].prediction == ""        # empty diff (no edit)
    assert rr.trajectories[0].success is False        # empty prediction -> miss
    assert rr.metrics.accuracy == 0.0


# --------------------------------------------------------------------------- #
# Test D — _make_grader auto routing (host-local, no container runtime)
# --------------------------------------------------------------------------- #
def test_make_grader_auto_routing() -> None:
    import argparse
    from memeval.claudecode import run_bench

    args = argparse.Namespace(grader="auto", grader_timeout=1800)
    # SWE benches -> LocalExecGrader; contextbench + QA benches -> None.
    assert isinstance(run_bench._make_grader("swe_contextbench", args), G.LocalExecGrader)
    assert isinstance(run_bench._make_grader("swe_bench_cl", args), G.LocalExecGrader)
    assert run_bench._make_grader("contextbench", args) is None      # retrieval-only
    assert run_bench._make_grader("longmemeval", args) is None       # QA
    assert run_bench._make_grader("memoryagentbench", args) is None  # QA
    # explicit none / local honored
    args_none = argparse.Namespace(grader="none", grader_timeout=1800)
    assert run_bench._make_grader("swe_contextbench", args_none) is None
    args_local = argparse.Namespace(grader="local", grader_timeout=1800)
    assert isinstance(run_bench._make_grader("longmemeval", args_local), G.LocalExecGrader)


def test_run_bench_code_mode_default_agentic() -> None:
    # The CLI default for --code-mode is 'agentic' (the genuine coding agent),
    # threaded into the constructed agent. Capture the parsed args by stubbing
    # _run_one so main() doesn't actually run a benchmark.
    from memeval.claudecode import run_bench

    captured: dict = {}
    orig = run_bench._run_one

    def _stub(benchmark, mode, args, **kw):
        captured["code_mode"] = args.code_mode
        captured["grader"] = args.grader
        return None

    run_bench._run_one = _stub  # type: ignore[assignment]
    try:
        run_bench.main(["--benchmark", "swe_contextbench", "--mode", "off",
                        "--results-dir", "", "--results",
                        str(Path(tempfile.gettempdir()) / "memeval-rb-defaults.json")])
    finally:
        run_bench._run_one = orig  # type: ignore[assignment]
    assert captured.get("code_mode") == "agentic"
    assert captured.get("grader") == "auto"


# --------------------------------------------------------------------------- #
# Test E — memory wired into the agentic CODE loop (plugin mode records retrieve)
# --------------------------------------------------------------------------- #
def test_agentic_code_plugin_records_retrieval() -> None:
    # plugin-mode agentic CODE: the fake claude calls the configured memory server
    # (reading the .mcp.json written into the CHECKOUT) and the agent attributes the
    # recall to the CODE trajectory — closing the "CODE bypasses memory" gap.
    import json as _json
    from memeval.okf import OKFStore
    from memeval.claudecode.service import MemoryService

    flag: dict = {}
    git = _make_fake_git(edited_flag=flag)

    def fake(prompt, *, cwd, mcp_config=None, allowed_tools=None, strict_mcp=False,
             permission_mode="bypassPermissions", **kw):
        # Edit the checkout (so a diff is produced) ...
        (Path(cwd) / "orm.py").write_text("def filter_empty():\n    return []\n",
                                          encoding="utf-8")
        flag["edited"] = True
        # ... and simulate retrieving from the configured memory server.
        cfg = _json.loads(Path(mcp_config).read_text(encoding="utf-8"))
        a = cfg["mcpServers"]["memeval-memory"]["args"]
        bundle = a[a.index("--bundle") + 1]
        log = a[a.index("--log") + 1]
        svc = MemoryService(OKFStore(bundle), log_path=log)
        svc.recall(prompt, k=5)
        return ClaudeResult(text="done", tokens_in=20, tokens_out=4)

    grader = G.LocalExecGrader(runner=_make_fake_cmd(), git_runner=git)
    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="plugin", code_mode="agentic",
                                runner=fake, git_runner=git, runtime=_NATIVE,
                                workdir=tmp, transport="stdio")
        rr = run_agent(Benchmark.SWE_CONTEXTBENCH, agent, memory=True,
                       path_or_id=_fixture("swe_contextbench.json"), limit=2,
                       seed_sessions=False, grader=grader)
    kinds = [s.kind for t in rr.trajectories for s in t.steps]
    assert "retrieve" in kinds      # CODE loop now exercises memory
    assert "generate" in kinds


# --------------------------------------------------------------------------- #
# Built-in runner (no pytest required)
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
