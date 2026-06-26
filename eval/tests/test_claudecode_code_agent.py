"""Offline end-to-end tests for the agentic CODE path + LocalExecGrader.

These prove the whole loop — `prepare_checkout` -> claude edits the checkout ->
`capture_diff` (the prediction) -> `LocalExecGrader` (apply patch + gold tests +
run) — runs DETERMINISTICALLY with **no network, no real `claude`, no container
runtime, no real git/venv**. The fixture `swe_contextbench.json` drives the
*task shape*; the checkout itself is synthesized by an injected fake git runner
(the fixture is not a real repo). A real swe_contextbench run additionally needs
network (clone by SHA), live `claude` subscription auth, and a buildable repo —
none available offline; that is the intentional limit of this suite (see
ADR-eval-002).

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
from memeval.claudecode.agent import (  # noqa: E402
    ClaudeCodeAgent,
    _PLUGIN_REAL_CODE_ALLOWED_TOOLS,
    _PLUGIN_REAL_RECALL_TOOL,
    _build_code_agent_prompt,
)
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


#: Tool names the plugin CODE turns are allowed to allowlist. The OKF-backed plugin
#: path may allowlist only its simulated memory tools; plugin-real may allowlist the
#: normal code tools plus the shipping plugin's recall tool.
_MEMORY_TOOLS = (
    "mcp__memeval-memory__memory_recall",
    "mcp__memeval-memory__memory_remember",
)


def _make_fake_claude(*, edited_flag: dict, edit: bool = True):
    """A fake claude runner that simulates the agent editing files in the checkout
    (writing the fixed orm.py) and flips ``edited_flag``. Asserts the agentic
    invariants: acceptEdits/bypass permission + a sanctioned toolset (either the
    full native toolset, the OKF plugin memory tools, or the plugin-real code tools
    plus shipping recall)."""

    def _fake_claude(prompt, *, cwd, permission_mode="bypassPermissions",
                     allowed_tools=None, append_system_prompt=None, **kw) -> ClaudeResult:
        assert permission_mode in ("acceptEdits", "bypassPermissions"), permission_mode
        allowed = set(_MEMORY_TOOLS) | set(_PLUGIN_REAL_CODE_ALLOWED_TOOLS)
        assert allowed_tools is None or set(allowed_tools) <= allowed, (
            "agentic CODE must use the full native toolset or an approved plugin "
            f"tool allowlist; got {allowed_tools!r}"
        )
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
            # selectors are the trailing args after the report flag (`-rA`); emit a
            # ``PASSED <nodeid>`` / ``FAILED <nodeid>`` summary line per selector,
            # matching pytest's `-rA` short-summary that _parse_pytest reads.
            flag = "-rA" if "-rA" in args else "-q"
            for sel in args[args.index(flag) + 1:]:
                if "empty" in sel or "index" in sel:
                    lines.append(f"{'PASSED' if fail_to_pass_passed else 'FAILED'} {sel}")
                else:
                    lines.append(f"{'PASSED' if pass_to_pass_passed else 'FAILED'} {sel}")
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


def test_capture_diff_excludes_memory_artifacts() -> None:
    """Memory artifacts live INSIDE the checkout; they must
    be excluded from BOTH the stage and the diff so the prediction is the clean CODE
    patch, never ``diff --git a/.cookbook-memory/.seeded ...`` or builtin
    ``CLAUDE.md`` / ``sessions`` files (which corrupt the patch the SWE-bench grader
    applies)."""
    calls: list = []

    def _recording_git(args, cwd, *a, **kw) -> GitResult:
        calls.append(list(args))
        if args and args[0] == "diff":
            return GitResult(returncode=0, stdout=_FIXED_DIFF)
        return GitResult(returncode=0)

    with tempfile.TemporaryDirectory() as tmp:
        diff = capture_diff(Path(tmp), git_runner=_recording_git)

    assert diff == _FIXED_DIFF
    add_calls = [c for c in calls if c and c[0] == "add"]
    diff_calls = [c for c in calls if c and c[0] == "diff"]
    assert add_calls and diff_calls, calls
    # Both the stage and the diff carry the :(exclude) pathspecs for memory artifacts.
    for exclude in (
        ":(exclude).cookbook-memory",
        ":(exclude)CLAUDE.md",
        ":(exclude)sessions",
    ):
        assert any(exclude in c for c in add_calls), add_calls
        assert exclude in diff_calls[0], diff_calls[0]


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


def test_grader_reverts_gold_test_files_before_applying_gold_patch() -> None:
    """Trust boundary: the gold test_patch's target files are git-reverted to base
    (discarding any agent edit) BEFORE the gold patch is applied, so an agent that
    touched a test file can't break the apply or influence the tests."""
    seen: list[list[str]] = []

    def _recording_git(args, cwd, *a, **kw):
        seen.append(list(args))
        # Re-materialize on init/checkout like the standard fake; everything else ok.
        if args and args[0] in ("init", "remote", "fetch", "checkout", "clone"):
            from pathlib import Path as _P
            _P(cwd).mkdir(parents=True, exist_ok=True)
            (_P(cwd) / "orm.py").write_text("x\n", encoding="utf-8")
        return GitResult(returncode=0)

    # A gold test_patch that targets a specific test file.
    task = _code_task(test_patch=(
        "diff --git a/testing/test_orm.py b/testing/test_orm.py\n"
        "--- a/testing/test_orm.py\n+++ b/testing/test_orm.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    ))
    g = G.LocalExecGrader(runner=_make_fake_cmd(), git_runner=_recording_git)
    g(task, _FIXED_DIFF)

    # A `git checkout -- testing/test_orm.py` (the revert) must appear, and it must
    # come BEFORE the gold-test `git apply` of that same file.
    revert_idxs = [i for i, a in enumerate(seen)
                   if a[:2] == ["checkout", "--"] and "testing/test_orm.py" in a]
    assert revert_idxs, f"expected a revert of the gold test file; saw {seen}"
    # patch_target_files extracts the right file from the test_patch.
    assert G.patch_target_files(task.test_patch) == ["testing/test_orm.py"]


def test_local_exec_grader_records_and_logs_reason_on_env_failure(caplog) -> None:
    # The degradation stays None (honesty rule) but is no longer SILENT: it sets
    # last_reason, tallies ungraded_reasons, and logs at WARNING.
    import logging
    git = _make_fake_git()
    cmd = _make_fake_cmd(raise_on_pytest=True)
    g = G.LocalExecGrader(runner=cmd, git_runner=git)
    with caplog.at_level(logging.WARNING, logger="memeval.grader"):
        assert g(_code_task(), _FIXED_DIFF) is None
    assert g.last_reason and "pytest" in g.last_reason
    assert sum(g.ungraded_reasons.values()) == 1
    assert any("UNGRADED" in r.message for r in caplog.records)


def test_local_exec_grader_reason_on_patch_apply_failure() -> None:
    git = _make_fake_git(apply_ok=False)
    g = G.LocalExecGrader(runner=_make_fake_cmd(), git_runner=git)
    assert g(_code_task(), _FIXED_DIFF) is None
    assert g.last_reason and "did not apply" in g.last_reason


def test_local_exec_grader_clears_reason_on_real_verdict() -> None:
    # A real verdict (True/False) leaves last_reason cleared (reset per call).
    g = G.LocalExecGrader(runner=_make_fake_cmd(), git_runner=_make_fake_git())
    assert g(_code_task(), _FIXED_DIFF) is True
    assert g.last_reason is None


# --------------------------------------------------------------------------- #
# Test B2 — agent._grade surfaces the grader's ungraded reason (visibility layer)
# --------------------------------------------------------------------------- #
# Reconciled design (ADR-eval-005/006): the grader exposes the *why* of a None via
# its ``last_reason`` (loud-degradation, main's design); agent._grade reads that and
# buckets it for the run histogram. These check the seam end to end.
def test_agent_grade_reports_graded_on_real_verdict() -> None:
    from memeval.agent import _grade

    git = _make_fake_git()
    cmd = _make_fake_cmd(fail_to_pass_passed=True, pass_to_pass_passed=True)
    g = G.LocalExecGrader(runner=cmd, git_runner=git)
    success, reason = _grade(_code_task(), _FIXED_DIFF, g)
    assert success is True and reason == "graded"


def test_agent_grade_buckets_env_failure_reason() -> None:
    from memeval.agent import _grade

    git = _make_fake_git()
    cmd = _make_fake_cmd(raise_on_pytest=True)  # env/build blows up -> None
    g = G.LocalExecGrader(runner=cmd, git_runner=git)
    success, reason = _grade(_code_task(), _FIXED_DIFF, g)
    assert success is None
    # grader.last_reason is a free-form string; agent buckets it to a stable label.
    assert reason in ("env_build_failed", "exception"), reason


def test_agent_grade_buckets_patch_apply_failure() -> None:
    from memeval.agent import _grade

    git = _make_fake_git(apply_ok=False)  # prediction patch won't apply -> None
    g = G.LocalExecGrader(runner=_make_fake_cmd(), git_runner=git)
    success, reason = _grade(_code_task(), _FIXED_DIFF, g)
    assert success is None and reason == "patch_apply_failed"


def test_bucket_ungraded_reason_maps_known_strings() -> None:
    from memeval.agent import _bucket_ungraded_reason

    assert _bucket_ungraded_reason("checkout failed: boom") == "checkout_failed"
    assert _bucket_ungraded_reason("gold test_patch did not apply") == \
        "gold_test_apply_failed"
    assert _bucket_ungraded_reason(None) == "ungraded"
    # An unrecognized reason is kept verbatim rather than lost.
    assert _bucket_ungraded_reason("weird novel reason") == "weird novel reason"


# --------------------------------------------------------------------------- #
# Test B3 — per-task env resolution: Python pin + setuptools-scm version gate
# --------------------------------------------------------------------------- #
def test_python_for_task_pins_known_repo() -> None:
    # 2019-era pytest must not run on a host-default 3.12+ (its `import imp` is gone).
    t = _code_task()
    t.repo = "pytest-dev/pytest"
    assert G._python_for_task(t) == "3.8"


def test_python_for_task_unknown_repo_is_host_default() -> None:
    t = _code_task()
    t.repo = "some/unmapped-repo"
    assert G._python_for_task(t) is None  # host default, legacy behavior


def test_python_for_task_metadata_override_wins() -> None:
    t = _code_task()
    t.repo = "pytest-dev/pytest"
    t.metadata = {**(t.metadata or {}), "python": "3.10"}
    assert G._python_for_task(t) == "3.10"


def test_scm_env_sets_pretend_version() -> None:
    # Tagless shallow checkout -> setuptools-scm would emit 0.1.dev1 and trip a
    # `requires pytest-2.0` minversion gate; the pretend version clears it.
    env = G._scm_env(_code_task())
    assert env.get("SETUPTOOLS_SCM_PRETEND_VERSION")
    assert env["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYTEST"] == \
        env["SETUPTOOLS_SCM_PRETEND_VERSION"]


def test_make_venv_passes_python_pin() -> None:
    # Record every argv the runner sees; assert `uv venv` carried `--python <ver>`.
    seen: list = []

    def _recording(args, cwd, env=None, *a, **kw):
        seen.append(list(args))
        return G.CmdResult(returncode=0)

    g = G.LocalExecGrader(runner=_recording)
    # _make_venv returns None here (the stub creates no interpreter on disk), but we
    # only care that the venv command requested the pin.
    g._make_venv("/tmp/x/repo", python="3.8")
    venv_cmds = [a for a in seen if a[:2] == ["uv", "venv"]]
    assert venv_cmds, "expected a `uv venv` invocation"
    assert "--python" in venv_cmds[0] and "3.8" in venv_cmds[0]


def test_make_venv_no_pin_omits_python_flag() -> None:
    seen: list = []

    def _recording(args, cwd, env=None, *a, **kw):
        seen.append(list(args))
        return G.CmdResult(returncode=0)

    g = G.LocalExecGrader(runner=_recording)
    g._make_venv("/tmp/x/repo", python=None)
    venv_cmds = [a for a in seen if a[:2] == ["uv", "venv"]]
    assert venv_cmds and "--python" not in venv_cmds[0]


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


def test_off_control_run_isolates_mcp() -> None:
    # The memoryless control (off) agentic CODE turn must pass strict_mcp=True (no
    # --mcp-config), so it ignores ALL installed MCP servers — it stays plugin-free even
    # if a concurrent plugin run installed the cookbook-memory plugin into the SHARED
    # sandbox config dir.
    seen: dict = {}
    flag: dict = {}
    git = _make_fake_git(edited_flag=flag)

    def fake(prompt, *, cwd, strict_mcp=False, mcp_config=None, **kw) -> ClaudeResult:
        seen["strict_mcp"] = strict_mcp
        seen["mcp_config"] = mcp_config
        (Path(cwd) / "orm.py").write_text("def f():\n    return []\n", encoding="utf-8")
        flag["edited"] = True
        return ClaudeResult(text="done", tokens_in=5, tokens_out=1)

    grader = G.LocalExecGrader(runner=_make_fake_cmd(), git_runner=git)
    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="off", code_mode="agentic",
                                runner=fake, git_runner=git, runtime=_NATIVE, workdir=tmp)
        run_agent(Benchmark.SWE_CONTEXTBENCH, agent, memory=False,
                  path_or_id=_fixture("swe_contextbench.json"), limit=1,
                  seed_sessions=False, grader=grader)
    assert seen["strict_mcp"] is True           # control isolates MCP
    assert seen["mcp_config"] is None            # ...and provides no MCP config


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

    from memeval.grader_swebench import SwebenchHostGrader

    args = argparse.Namespace(grader="auto", grader_timeout=1800)
    # SWE benches under `auto`: the swebench host grader WHEN the optional
    # `swebench` package is importable, else LocalExecGrader (run_bench:_swebench
    # _available). Assert against that contract so the test is correct whether or
    # not the extra is installed in the running env.
    swe_cls = (SwebenchHostGrader if run_bench._swebench_available()
               else G.LocalExecGrader)
    assert isinstance(run_bench._make_grader("swe_contextbench", args), swe_cls)
    assert isinstance(run_bench._make_grader("swe_bench_cl", args), swe_cls)
    assert run_bench._make_grader("contextbench", args) is None      # retrieval-only
    assert run_bench._make_grader("longmemeval", args) is None       # QA
    assert run_bench._make_grader("memoryagentbench", args) is None  # QA
    # explicit none / local honored
    args_none = argparse.Namespace(grader="none", grader_timeout=1800)
    assert run_bench._make_grader("swe_contextbench", args_none) is None
    args_local = argparse.Namespace(grader="local", grader_timeout=1800)
    assert isinstance(run_bench._make_grader("longmemeval", args_local), G.LocalExecGrader)
    # 'swebench' routes to SwebenchHostGrader AND forwards --grader-timeout to it.
    from memeval.grader_swebench import SwebenchHostGrader
    args_swe = argparse.Namespace(grader="swebench", grader_timeout=4242)
    g = run_bench._make_grader("swe_bench_cl", args_swe)
    assert isinstance(g, SwebenchHostGrader)
    assert g.timeout == 4242


def test_swebench_grader_venv_root_is_cleaned_up() -> None:
    # The shared per-sequence venv root is a temp dir; cleanup() removes it (so whole
    # venvs aren't leaked under /tmp) and is idempotent. Exercised without the swebench
    # extra — _venv_root_dir/cleanup touch no swebench API.
    from memeval.grader_swebench import SwebenchHostGrader
    g = SwebenchHostGrader()
    root = g._venv_root_dir()
    assert root.is_dir()
    g.cleanup()
    assert not root.is_dir() and g._venv_root is None
    g.cleanup()  # idempotent — no error on a second call


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
    # plugin-mode agentic CODE: the turn is driven through the primed + retry-until-
    # recall path (mirroring _run_plugin_http). The fake claude calls the configured
    # memory server (reading the .mcp.json written into the CHECKOUT) so the recall
    # log gains a recall op, the loop sees it and stops, and the agent attributes the
    # recall to the CODE trajectory — closing the "CODE bypasses memory" gap.
    # NOTE: under the injected fake runner, _run_primed falls back to a plain call
    # (priming only engages when self._runner is run_claude), so this also verifies
    # the fallback still reaches memory.
    import json as _json
    from memeval.okf import OKFStore
    from memeval.claudecode.service import MemoryService

    flag: dict = {}
    calls: dict = {"n": 0, "tools": None, "permission": None}
    git = _make_fake_git(edited_flag=flag)

    def fake(prompt, *, cwd, mcp_config=None, allowed_tools=None, strict_mcp=False,
             permission_mode="bypassPermissions", **kw):
        calls["n"] += 1
        calls["tools"] = allowed_tools
        calls["permission"] = permission_mode
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
    assert "retrieve" in kinds      # CODE loop now exercises memory (primed+retry)
    assert "generate" in kinds
    # The plugin CODE turn must allowlist the memory MCP tools and keep acceptEdits.
    assert calls["tools"] == list(_MEMORY_TOOLS)
    assert calls["permission"] == "acceptEdits"
    # Recall fired on the first try, so the retry loop stops after one call per task
    # (2 tasks -> exactly 2 runner invocations, no wasteful retries).
    assert calls["n"] == 2


def test_agentic_code_plugin_real_records_retrieval(monkeypatch) -> None:
    # plugin-real-mode agentic CODE = the SHIPPING plugin (cookbook-memory) as a
    # black box. The fake CLI stands in for the installed plugin: it edits the
    # checkout (so a diff is produced) AND writes a recall event (with meta.hits) to
    # the plugin's OWN events stream under ${CLAUDE_PROJECT_DIR}/.cookbook-memory —
    # exactly as the cookbook-memory MCP server would. The agent now drives this turn
    # through the primed + retry-until-recall loop (mirroring the QA plugin-real
    # path) and attributes the retrieval to the CODE trajectory from meta.hits.
    #
    # Stdlib-only: plugin-real seeds through `memory-cli` (a subprocess that no-ops
    # when the plugin isn't installed) and uses a fake runner, so no `cookbook_memory`
    # import and no real `claude`. Under the fake runner _ensure_real_plugin returns
    # {} and seeding no-ops, and _run_primed falls back to a plain call (priming only
    # engages when self._runner is run_claude) — so this also verifies the fallback
    # still reaches the plugin's recall.
    #
    # Force the NO-sandbox branch so the --allowedTools fallback (the explicit
    # allowlist) is exercised deterministically regardless of whether a sandbox dir
    # happens to exist on this machine. The sandbox-active branch (allowed_tools=None)
    # is covered by test_plugin_real_allowed_tools_none_when_sandbox_active.
    import memeval.claudecode.sandbox as _sandbox
    monkeypatch.setattr(_sandbox, "active_config_dir", lambda: None)
    import json as _json

    flag: dict = {}
    calls: dict = {"n": 0, "tools": "UNSET", "permission": None}
    git = _make_fake_git(edited_flag=flag)

    def fake(prompt, *, cwd, permission_mode="bypassPermissions", allowed_tools=None,
             strict_mcp=True, **kw):
        calls["n"] += 1
        calls["tools"] = allowed_tools
        calls["permission"] = permission_mode
        calls["strict_mcp"] = strict_mcp
        # Edit the checkout (so a diff is produced) ...
        (Path(cwd) / "orm.py").write_text("def filter_empty():\n    return []\n",
                                          encoding="utf-8")
        flag["edited"] = True
        # ... and simulate the installed plugin's MCP server logging a recall to its
        # own events stream under the checkout's .cookbook-memory dir.
        store = Path(cwd) / ".cookbook-memory"
        store.mkdir(parents=True, exist_ok=True)
        ev = {
            "ts": 1.0, "op": "recall", "ids": ["m1"], "query": prompt,
            "meta": {"hits": [{"id": "m1", "content": "return [] not None",
                               "score": 0.9, "rank": 0, "tokens": 4, "timestamp": 1.0}]},
        }
        # Append so multiple tasks accumulate; the loop counts events per task.
        with open(store / "events.jsonl", "a", encoding="utf-8") as fh:
            fh.write(_json.dumps(ev) + "\n")
        return ClaudeResult(text="done", tokens_in=20, tokens_out=4)

    grader = G.LocalExecGrader(runner=_make_fake_cmd(), git_runner=git)
    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="plugin-real", code_mode="agentic",
                                runner=fake, git_runner=git, runtime=_NATIVE,
                                workdir=tmp)
        rr = run_agent(Benchmark.SWE_CONTEXTBENCH, agent, memory=True,
                       path_or_id=_fixture("swe_contextbench.json"), limit=2,
                       seed_sessions=False, grader=grader)
    kinds = [s.kind for t in rr.trajectories for s in t.steps]
    assert "retrieve" in kinds      # CODE loop now exercises the SHIPPING plugin
    assert "generate" in kinds
    # plugin-real explicitly allows normal code tools plus the shipping plugin recall
    # tool, otherwise headless Claude denies the MCP call.
    assert calls["tools"] == _PLUGIN_REAL_CODE_ALLOWED_TOOLS
    assert _PLUGIN_REAL_RECALL_TOOL in calls["tools"]
    assert calls["permission"] == "acceptEdits"
    # plugin-real must NOT isolate MCP — it relies on the INSTALLED plugin's server, so
    # strict_mcp is False (the control run is the one that isolates MCP).
    assert calls["strict_mcp"] is False
    # Recall fired on the first try, so the retry loop stops after one call per task
    # (2 tasks -> exactly 2 runner invocations, no wasteful retries).
    assert calls["n"] == 2


def test_agentic_code_plugin_real_natural_unprimed_does_not_force_recall(monkeypatch) -> None:
    import memeval.claudecode.sandbox as _sandbox
    monkeypatch.setattr(_sandbox, "active_config_dir", lambda: None)

    flag: dict = {}
    calls: dict = {"n": 0, "prompt": "", "strict_mcp": None, "permission": None}
    git = _make_fake_git(edited_flag=flag)

    def fake(prompt, *, cwd, permission_mode="bypassPermissions", strict_mcp=True, **kw):
        calls["n"] += 1
        calls["prompt"] = prompt
        calls["strict_mcp"] = strict_mcp
        calls["permission"] = permission_mode
        (Path(cwd) / "orm.py").write_text("def filter_empty():\n    return []\n",
                                          encoding="utf-8")
        flag["edited"] = True
        return ClaudeResult(text="done", tokens_in=20, tokens_out=4)

    grader = G.LocalExecGrader(runner=_make_fake_cmd(), git_runner=git)
    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(
            memory_mode="plugin-real", code_mode="agentic", runner=fake,
            git_runner=git, runtime=_NATIVE, workdir=tmp,
        )
        rr = run_agent(Benchmark.SWE_CONTEXTBENCH, agent, memory=True,
                       path_or_id=_fixture("swe_contextbench.json"), limit=1,
                       seed_sessions=False, grader=grader)

    kinds = [s.kind for t in rr.trajectories for s in t.steps]
    assert "generate" in kinds
    assert "retrieve" not in kinds
    assert calls["n"] == 1
    assert "Persistent memory is available through recall if prior fixes would help" in calls["prompt"]
    assert "First call the recall tool" not in calls["prompt"]
    assert calls["permission"] == "acceptEdits"
    assert calls["strict_mcp"] is False


# --------------------------------------------------------------------------- #
# Daydream write accounting (Fix #2) + group-scoped substrate (Fix #1)
# --------------------------------------------------------------------------- #
def test_count_daydream_writes_ignores_hook_fired() -> None:
    """The Stop-hook FIRING is not a write. Counting it made the drain barrier skip the
    backstop and leave the substrate empty — regression-guard that fix."""
    import json
    from memeval.claudecode.agent import _count_daydream_writes
    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / ".cookbook-memory"
        store.mkdir()
        # Only a hook-fired marker present -> NOT a write.
        (store / "events.jsonl").write_text(
            json.dumps({"op": "daydream.hook_subprocess_fired"}) + "\n", encoding="utf-8")
        assert _count_daydream_writes(store) == 0


def test_count_daydream_writes_counts_markdown_and_diary() -> None:
    import json
    from memeval.claudecode.agent import _count_daydream_writes
    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / ".cookbook-memory"
        # Primary signal: markdown memory files.
        md = store / "markdown" / "daydream"
        md.mkdir(parents=True)
        (md / "mem_a.md").write_text("x", encoding="utf-8")
        (md / "mem_b.md").write_text("y", encoding="utf-8")
        assert _count_daydream_writes(store) == 2
    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / ".cookbook-memory"
        dream = store / "dream"
        dream.mkdir(parents=True)
        # Fallback signal: memory_written diary events (keyed by event_type).
        (dream / "s.daydream-events.jsonl").write_text("\n".join(json.dumps(r) for r in [
            {"event_type": "daydream.memory_written", "item_id": "m1"},
            {"event_type": "daydream.candidate_rejected"},
            {"event_type": "daydream.memory_written", "item_id": "m2"},
        ]) + "\n", encoding="utf-8")
        assert _count_daydream_writes(store) == 2


def test_plugin_real_store_group_scoped_accumulates_per_sequence() -> None:
    """plugin-real CL benchmark: same sequence -> one shared store (accumulates);
    different sequence -> isolated store."""
    with tempfile.TemporaryDirectory() as tmp:
        sub = Path(tmp) / "_memory"
        a = ClaudeCodeAgent(memory_mode="plugin-real", project_dir=sub,
                            group_scoped_store=True)
        _, sd1 = a._plugin_real_store(Path(tmp) / "co1", group_id="django_django_sequence")
        _, sd2 = a._plugin_real_store(Path(tmp) / "co2", group_id="django_django_sequence")
        _, sd3 = a._plugin_real_store(Path(tmp) / "co3", group_id="sympy_sympy_sequence")
        assert sd1 == sd2          # same sequence -> carryover
        assert sd3 != sd1          # different sequence -> isolated
        assert sd1 == (sub / "django_django_sequence" / ".cookbook-memory").resolve()


def test_plugin_real_store_flat_when_not_group_scoped() -> None:
    """pipeline.py path (group_scoped_store=False) keeps the flat substrate unchanged."""
    with tempfile.TemporaryDirectory() as tmp:
        sub = Path(tmp) / "_memory"
        a = ClaudeCodeAgent(memory_mode="plugin-real", project_dir=sub)
        pd, sd = a._plugin_real_store(Path(tmp) / "co", group_id="any_sequence")
        assert pd == sub.resolve()                       # group ignored
        assert sd == (sub / ".cookbook-memory").resolve()


def test_plugin_real_allowed_tools_none_when_sandbox_active(monkeypatch) -> None:
    """With a sandbox active, its settings.json grants the recall tool, so plugin-real
    passes NO --allowedTools — the SAME CLI as the no-plugin control."""
    import memeval.claudecode.sandbox as S
    a = ClaudeCodeAgent(memory_mode="plugin-real")
    monkeypatch.setattr(S, "active_config_dir", lambda: "/some/sandbox")
    assert a._plugin_real_allowed_tools(["X", "Y"]) is None


def test_plugin_real_allowed_tools_falls_back_without_sandbox(monkeypatch) -> None:
    """Without a sandbox (the MEMEVAL_SANDBOX=0 opt-out) there is no settings grant, so
    the explicit allowlist is used so headless recall isn't denied."""
    import memeval.claudecode.sandbox as S
    a = ClaudeCodeAgent(memory_mode="plugin-real")
    monkeypatch.setattr(S, "active_config_dir", lambda: None)
    assert a._plugin_real_allowed_tools(["X", "Y"]) == ["X", "Y"]


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
