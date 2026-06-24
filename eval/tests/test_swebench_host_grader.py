"""Offline, deterministic tests for :class:`SwebenchHostGrader`.

These prove the grader integrates FAITHFULLY with SWE-bench's own building
blocks — the REAL ``get_test_directives``, the REAL ``MAP_REPO_TO_PARSER`` django
parser, and the REAL grading fold (``get_eval_tests_report`` +
``get_resolution_status``) — by faking ONLY the command runner and git. No
network, no real ``uv`` venv, no Docker, no real django checkout.

The canned log is a real django ``runtests.py --verbosity 2`` transcript whose
``... ok`` / ``FAIL:`` lines the official ``parse_log_django`` reads. The grading
verdict is therefore produced by SWE-bench's own code, not by the test.

Skips cleanly if the optional ``swebench`` package is not importable.

Run with: ``env -u OPENROUTER_API_KEY PYTHONPATH=. <py> -m pytest
tests/test_swebench_host_grader.py``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Optional

_THIS = Path(__file__).resolve()
_BASE_DIR = _THIS.parent.parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

import pytest  # noqa: E402

swebench = pytest.importorskip(
    "swebench", reason="optional 'swebench' extra not installed")

from memeval.schema import Benchmark, Task, TaskKind  # noqa: E402
from memeval.claudecode.checkout import GitResult  # noqa: E402
from memeval.grader import CmdResult, get_grader  # noqa: E402
from memeval.grader_swebench import SwebenchHostGrader  # noqa: E402


# --------------------------------------------------------------------------- #
# Real-ish django instance shape (a version present in MAP_REPO_VERSION_TO_SPECS).
# --------------------------------------------------------------------------- #
_REPO = "django/django"
_VERSION = "3.0"

# Canonical SWE-bench django selector form: "method (dotted.Class)".
_F2P = ["test_ticket_14056 (queries.tests.Queries1Tests)"]
_P2P = [
    "test_in_query (queries.tests.Queries1Tests)",
    "test_ticket_19672 (queries.tests.Queries1Tests)",
]

# A gold test_patch touching tests/queries/tests.py -> get_test_directives yields
# the django module directive "queries.tests" (verified against the real fn).
_TEST_PATCH = (
    "diff --git a/tests/queries/tests.py b/tests/queries/tests.py\n"
    "--- a/tests/queries/tests.py\n"
    "+++ b/tests/queries/tests.py\n"
    "@@ -1,1 +1,2 @@\n"
    " # existing\n"
    "+# added by gold test patch\n"
)

_PREDICTION = (
    "diff --git a/django/db/models/query.py b/django/db/models/query.py\n"
    "--- a/django/db/models/query.py\n"
    "+++ b/django/db/models/query.py\n"
    "@@ -1,1 +1,1 @@\n"
    "-    return None\n"
    "+    return self._fixed()\n"
)

# Official-format django runtests transcripts. parse_log_django keys on "... ok"
# (pass) and "FAIL: <test>" banners (fail). Validated against the real parser.
_LOG_ALL_PASS = (
    "Testing started at 12:00 ...\n"
    "test_ticket_14056 (queries.tests.Queries1Tests) ... ok\n"
    "test_in_query (queries.tests.Queries1Tests) ... ok\n"
    "test_ticket_19672 (queries.tests.Queries1Tests) ... ok\n"
    "----------------------------------------------------------------------\n"
    "Ran 3 tests in 0.123s\n"
    "\n"
    "OK\n"
)

_LOG_F2P_FAILS = (
    "test_ticket_14056 (queries.tests.Queries1Tests) ... FAIL\n"
    "test_in_query (queries.tests.Queries1Tests) ... ok\n"
    "test_ticket_19672 (queries.tests.Queries1Tests) ... ok\n"
    "======================================================================\n"
    "FAIL: test_ticket_14056 (queries.tests.Queries1Tests)\n"
    "----------------------------------------------------------------------\n"
    "Traceback (most recent call last):\n"
    "AssertionError: still broken\n"
    "----------------------------------------------------------------------\n"
    "Ran 3 tests in 0.10s\n"
    "\n"
    "FAILED (failures=1)\n"
)


def _make_task(repo: str = _REPO, version: Optional[str] = _VERSION) -> Task:
    meta = {"version": version} if version is not None else {}
    return Task(
        task_id="django__django-11000",
        benchmark=Benchmark.SWE_BENCH_CL,
        kind=TaskKind.CODE,
        question="Fix the queryset bug.",
        repo=repo,
        base_commit="0" * 40,
        patch="",  # gold patch (unused by the grader's verdict)
        test_patch=_TEST_PATCH,
        fail_to_pass=list(_F2P),
        pass_to_pass=list(_P2P),
        metadata=meta,
    )


def _make_fake_git(*, apply_ok: bool = True):
    """Fake GitRunner: materializes a stub checkout on disk; ``apply`` honors
    ``apply_ok``. No real git, no network."""

    def _fake_git(args, cwd, *a, **kw) -> GitResult:
        cwd = Path(cwd)
        op = args[0] if args else ""
        if op in ("init", "remote", "fetch", "checkout", "clone"):
            cwd.mkdir(parents=True, exist_ok=True)
            tests_dir = cwd / "tests" / "queries"
            tests_dir.mkdir(parents=True, exist_ok=True)
            (tests_dir / "tests.py").write_text("# existing\n", encoding="utf-8")
            (cwd / "tests" / "runtests.py").write_text("# runner\n", encoding="utf-8")
            return GitResult(returncode=0)
        if op == "apply":
            return GitResult(returncode=0 if apply_ok else 1,
                             stderr="" if apply_ok else "patch does not apply")
        return GitResult(returncode=0)

    return _fake_git


def _make_fake_cmd(*, log: str):
    """Fake CmdRunner. The test command (django ``runtests.py``) returns the canned
    official-format ``log``; uv/venv/install commands report success (and create no
    files, so the grader falls back to a configured python — exercising the
    no-real-venv path)."""

    def _fake_cmd(args, cwd, env=None, *a, **kw) -> CmdResult:
        joined = " ".join(str(a) for a in args)
        if "runtests.py" in joined:
            rc = 0 if "FAIL" not in log else 1
            return CmdResult(returncode=rc, stdout=log, stderr="")
        # uv venv / uv pip install / editable install: succeed, write nothing.
        return CmdResult(returncode=0)

    return _fake_cmd


class SwebenchHostGraderTests(unittest.TestCase):
    def _grader(self, *, apply_ok=True, log=_LOG_ALL_PASS) -> SwebenchHostGrader:
        return SwebenchHostGrader(
            runner=_make_fake_cmd(log=log),
            git_runner=_make_fake_git(apply_ok=apply_ok),
            python_exe="python",  # fallback when the stub venv writes no interpreter
        )

    def test_resolved_true_when_all_pass(self):
        """All F2P + P2P passing -> RESOLVED (True), via the real grading fold."""
        g = self._grader(log=_LOG_ALL_PASS)
        verdict = g(_make_task(), _PREDICTION)
        self.assertIs(verdict, True)
        self.assertIsNone(g.last_reason)

    def test_false_when_fail_to_pass_fails(self):
        """A FAIL_TO_PASS shown failing -> not resolved (False), not None."""
        g = self._grader(log=_LOG_F2P_FAILS)
        verdict = g(_make_task(), _PREDICTION)
        self.assertIs(verdict, False)
        self.assertIsNone(g.last_reason)

    def test_none_when_no_spec_for_repo_version(self):
        """Unknown repo/version (no MAP_REPO_VERSION_TO_SPECS entry) -> UNGRADED."""
        g = self._grader()
        task = _make_task(version="0.0-nonexistent")
        verdict = g(task, _PREDICTION)
        self.assertIsNone(verdict)
        self.assertIsNotNone(g.last_reason)
        self.assertIn("no swebench spec", g.last_reason)
        self.assertEqual(g.ungraded_reasons.get(g.last_reason), 1)

    def test_none_when_prediction_patch_does_not_apply(self):
        """Prediction patch failing to apply -> UNGRADED (base drift), not False."""
        g = self._grader(apply_ok=False)
        verdict = g(_make_task(), _PREDICTION)
        self.assertIsNone(verdict)
        self.assertIsNotNone(g.last_reason)
        self.assertIn("did not apply", g.last_reason)

    def test_empty_prediction_is_real_miss(self):
        """Empty prediction = no patch produced = a real miss (False), not UNGRADED."""
        g = self._grader()
        self.assertIs(g(_make_task(), "   "), False)

    def test_non_code_task_returns_none(self):
        """A QA task is not this grader's job -> None (not a degradation)."""
        g = self._grader()
        task = _make_task()
        task.kind = TaskKind.QA
        self.assertIsNone(g(task, _PREDICTION))
        self.assertIsNone(g.last_reason)

    def test_registry_resolves_swebench_keys(self):
        """get_grader routes the swebench aliases to SwebenchHostGrader."""
        for key in ("swebench", "swebench-host", "swebenchhost"):
            self.assertIsInstance(get_grader(key), SwebenchHostGrader)


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- #
# Python-pin fallback: uv can't fetch old pins (3.6/3.7); substitute nearest >= pin.
# --------------------------------------------------------------------------- #
def test_make_venv_falls_back_to_nearest_uv_python(tmp_path) -> None:
    """A pinned python uv cannot provision (3.6) must NOT leave the task ungraded when a
    newer uv-available python (3.8) exists — the grader substitutes the nearest one and
    records it, instead of failing with 'could not provision python 3.6'."""
    calls: list = []

    def fake_runner(args, cwd, env=None):
        calls.append(list(args))
        if args[:2] == ["uv", "venv"]:
            ver = args[args.index("--python") + 1] if "--python" in args else ""
            if ver == "3.6":
                return CmdResult(returncode=1, stderr="No download found for 3.6")
            venv = Path(args[-1])
            (venv / "bin").mkdir(parents=True, exist_ok=True)
            (venv / "bin" / "python").write_text("#!stub\n", encoding="utf-8")
            return CmdResult(returncode=0)
        if args[:3] == ["uv", "python", "list"]:
            return CmdResult(returncode=0, stdout=(
                "cpython-3.11.9-linux-x86_64-gnu   <download available>\n"
                "cpython-3.8.20-linux-x86_64-gnu   <download available>\n"))
        return CmdResult(returncode=0)

    g = SwebenchHostGrader(runner=fake_runner)
    dest = tmp_path / "repo"
    dest.mkdir()
    task = Task(task_id="django__django-9296", benchmark=Benchmark.SWE_BENCH_CL,
                kind=TaskKind.CODE, question="x")
    py = g._make_venv(dest, python="3.6", task=task)

    assert py is not None and py.replace("\\", "/").endswith("bin/python")
    # Substituted the SMALLEST available >= pin (3.8, not 3.11), and recorded it.
    assert g.python_substitutions["django__django-9296"] == "3.6->3.8"
    assert any(c[:2] == ["uv", "venv"] and "3.6" in c for c in calls)   # tried the pin
    assert any(c[:3] == ["uv", "python", "list"] for c in calls)        # consulted uv
    assert any(c[:2] == ["uv", "venv"] and "3.8" in c for c in calls)   # used 3.8


def test_make_venv_offline_stub_no_fallback(tmp_path) -> None:
    """Offline stub (uv venv rc 0 but writes no interpreter) returns None WITHOUT a
    fallback search — keeps the faked-runner grading tests unchanged."""
    calls: list = []

    def fake_runner(args, cwd, env=None):
        calls.append(list(args))
        return CmdResult(returncode=0)  # rc 0 but never creates venv/bin/python

    g = SwebenchHostGrader(runner=fake_runner)
    dest = tmp_path / "repo"
    dest.mkdir()
    assert g._make_venv(dest, python="3.6") is None
    assert not any(c[:3] == ["uv", "python", "list"] for c in calls)  # no fallback search
