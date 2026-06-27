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
from memeval.grader_swebench import (  # noqa: E402
    SwebenchDockerGrader,
    SwebenchHostGrader,
    _scm_env,
    _split_env_prefix,
)


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

    def test_registry_resolves_swebench_docker_keys(self):
        """get_grader routes docker aliases to the opt-in Docker grader."""
        for key in ("swebench-docker", "swebenchdocker", "docker"):
            self.assertIsInstance(get_grader(key), SwebenchDockerGrader)


    def test_with_python_routes_each_test_cmd_head(self):
        """``_with_python`` maps a spec ``test_cmd``'s argv onto the venv interpreter.
        The sphinx/tox case is the I-11 regression: ``tox`` must run as ``py -m tox``
        with its own flags (``--current-env -epy39 -v --``) INTACT — NOT be rewritten
        to ``py -m pytest`` with tox-only flags leaking through to pytest (which exits
        usage-error -> empty log -> 'official parser produced no statuses')."""
        g = self._grader()
        cases = {
            # sphinx: tox stays tox, all flags preserved.
            ("tox", "--current-env", "-epy39", "-v", "--", "tests/foo.py"):
                ["PY", "-m", "tox", "--current-env", "-epy39", "-v", "--", "tests/foo.py"],
            # matplotlib/sklearn/xarray/pytest: plain pytest.
            ("pytest", "-rA", "tests/foo.py"):
                ["PY", "-m", "pytest", "-rA", "tests/foo.py"],
            # pytest-3 is just pytest under another name.
            ("pytest-3", "-rA", "tests/foo.py"):
                ["PY", "-m", "pytest", "-rA", "tests/foo.py"],
            # sympy: a path-like script runs THROUGH the interpreter (no +x/shebang).
            ("bin/test", "-C", "--verbose", "sympy/core/tests/test_x.py"):
                ["PY", "bin/test", "-C", "--verbose", "sympy/core/tests/test_x.py"],
            # django: a .py runner runs through the interpreter.
            ("./tests/runtests.py", "--verbosity", "2", "queries"):
                ["PY", "./tests/runtests.py", "--verbosity", "2", "queries"],
        }
        for argv, expected in cases.items():
            self.assertEqual(g._with_python("PY", list(argv)), expected, msg=argv[0])


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- #
# Opt-in Docker grader adapter. These tests fake the Docker/SWE-bench callables:
# no daemon, no network, no real images.
# --------------------------------------------------------------------------- #
class _FakeDockerClient:
    def ping(self):
        return True


class _FakeTestSpec:
    instance_id = "django__django-11000"


def test_swebench_docker_grader_delegates_to_official_run_instance_shape():
    calls = {}

    def fake_make_test_spec(instance, namespace=None):
        calls["instance"] = instance
        calls["namespace"] = namespace
        return _FakeTestSpec()

    def fake_build_env_images(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("remote namespace should not build env images locally")

    def fake_run_instance(test_spec, pred, rm_image, force_rebuild, client, run_id, timeout):
        calls["run"] = {
            "test_spec": test_spec,
            "pred": pred,
            "rm_image": rm_image,
            "force_rebuild": force_rebuild,
            "client": client,
            "run_id": run_id,
            "timeout": timeout,
        }
        return {"completed": True, "resolved": True}

    g = SwebenchDockerGrader(
        timeout=123,
        docker_client=_FakeDockerClient(),
        make_test_spec=fake_make_test_spec,
        build_env_images=fake_build_env_images,
        run_instance=fake_run_instance,
    )

    assert g(_make_task(), _PREDICTION) is True
    assert g.last_reason is None
    assert calls["namespace"] == "swebench"
    assert calls["instance"]["repo"] == _REPO
    assert calls["instance"]["version"] == _VERSION
    assert calls["instance"]["FAIL_TO_PASS"] == _F2P
    assert calls["run"]["pred"]["instance_id"] == "django__django-11000"
    assert calls["run"]["pred"]["model_patch"] == _PREDICTION
    assert calls["run"]["timeout"] == 123
    assert calls["run"]["run_id"].startswith("memeval-swe-docker-")


def test_swebench_docker_grader_local_namespace_builds_env_first():
    calls = {"builds": 0}

    def fake_make_test_spec(instance, namespace=None):
        calls["namespace"] = namespace
        return _FakeTestSpec()

    def fake_build_env_images(client, dataset, **kwargs):
        calls["builds"] += 1
        calls["build_dataset"] = dataset
        calls["build_kwargs"] = kwargs
        return [], []

    def fake_run_instance(*args, **kwargs):
        return {"completed": True, "resolved": False}

    g = SwebenchDockerGrader(
        namespace="none",
        force_rebuild=True,
        docker_client=_FakeDockerClient(),
        make_test_spec=fake_make_test_spec,
        build_env_images=fake_build_env_images,
        run_instance=fake_run_instance,
    )

    assert g(_make_task(), _PREDICTION) is False
    assert calls["namespace"] is None
    assert calls["builds"] == 1
    assert calls["build_dataset"][0]["repo"] == _REPO
    assert calls["build_kwargs"]["force_rebuild"] is True
    assert calls["build_kwargs"]["namespace"] is None


def test_swebench_docker_grader_incomplete_run_is_ungraded():
    g = SwebenchDockerGrader(
        docker_client=_FakeDockerClient(),
        make_test_spec=lambda instance, namespace=None: _FakeTestSpec(),
        build_env_images=lambda *a, **kw: ([], []),
        run_instance=lambda *a, **kw: {"completed": False, "resolved": False},
    )

    assert g(_make_task(), _PREDICTION) is None
    assert g.last_reason == "swebench Docker run did not complete"
    assert g.ungraded_reasons[g.last_reason] == 1


# --------------------------------------------------------------------------- #
# Python-pin fallback: uv can't fetch old pins (3.5/3.6/3.7); use exact external
# interpreters when available, and substitute nearest >= pin only when explicitly
# opted in.
# --------------------------------------------------------------------------- #
def test_make_venv_uses_exact_external_python_before_substitution(tmp_path) -> None:
    """A pinned python uv cannot provision (3.6), but an exact external interpreter can
    still make the task gradeable without host-substituting Python 3.8."""
    calls: list = []

    def fake_runner(args, cwd, env=None):
        calls.append(list(args))
        if args[:2] == ["uv", "venv"]:
            ver = args[args.index("--python") + 1] if "--python" in args else ""
            if ver == "3.6":
                return CmdResult(returncode=1, stderr="No download found for 3.6")
            if ver == "/opt/pythons/3.6/bin/python":
                venv = Path(args[-1])
                (venv / "bin").mkdir(parents=True, exist_ok=True)
                (venv / "bin" / "python").write_text("#!stub\n", encoding="utf-8")
                return CmdResult(returncode=0)
        if args[:3] == ["uv", "python", "list"]:
            raise AssertionError("nearest-version substitution should not run")
        return CmdResult(returncode=0)

    g = SwebenchHostGrader(
        runner=fake_runner,
        python_exes={"3.6": "/opt/pythons/3.6/bin/python"},
        allow_python_substitution=True,
    )
    dest = tmp_path / "repo"
    dest.mkdir()
    py = g._make_venv(dest, python="3.6")

    assert py is not None and py.replace("\\", "/").endswith("bin/python")
    assert any(c[:2] == ["uv", "venv"] and "3.6" in c for c in calls)
    assert any(c[:2] == ["uv", "venv"] and "/opt/pythons/3.6/bin/python" in c
               for c in calls)
    assert g.python_substitutions == {}


def test_make_venv_can_disable_python_substitution(tmp_path) -> None:
    """Unsupported old pins can be forced to exact-interpreter-only grading."""
    calls: list = []

    def fake_runner(args, cwd, env=None):
        calls.append(list(args))
        if args[:2] == ["uv", "venv"]:
            return CmdResult(returncode=1, stderr="No download found")
        if args[:3] == ["uv", "python", "list"]:
            raise AssertionError("nearest-version substitution should be opt-in")
        return CmdResult(returncode=0)

    g = SwebenchHostGrader(runner=fake_runner, allow_python_substitution=False)
    dest = tmp_path / "repo"
    dest.mkdir()

    assert g._make_venv(dest, python="3.6") is None
    assert not any(c[:3] == ["uv", "python", "list"] for c in calls)


def test_make_venv_substitutes_nearest_uv_python_by_default(tmp_path) -> None:
    """A pinned python uv cannot provision (3.6) can still be graded under a newer
    uv-available python by default, preserving host gradeability for old Django pins."""
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


# --------------------------------------------------------------------------- #
# Host-vs-conda base env: a fresh uv venv lacks pip (so spec installs assuming
# ``python -m pip`` fail -> no pytest -> empty log -> 'no statuses'), and ``--seed``
# installs the LATEST setuptools/docutils which break OLD repo eras.
# --------------------------------------------------------------------------- #
def _seed_probe_runner(calls):
    """A CmdRunner that records calls and materializes a stub venv interpreter."""

    def r(args, cwd, env=None):
        calls.append(list(args))
        if args[:2] == ["uv", "venv"]:
            venv = Path(args[-1])
            (venv / "bin").mkdir(parents=True, exist_ok=True)
            (venv / "bin" / "python").write_text("#!stub\n", encoding="utf-8")
        return CmdResult(returncode=0)

    return r


def test_make_venv_seed_is_opt_in(tmp_path) -> None:
    """``--seed`` (pip/setuptools/wheel) is added to the venv ONLY when ``seed=True``.
    A repo that needs conda-base parity gets pip; every OTHER repo's venv is created
    byte-identically to before (no cross-benchmark behavior change)."""
    seeded: list = []
    g = SwebenchHostGrader(runner=_seed_probe_runner(seeded))
    d1 = tmp_path / "seeded"
    d1.mkdir()
    g._make_venv(d1, python="3.9", seed=True)
    venv1 = [c for c in seeded if c[:2] == ["uv", "venv"]]
    assert venv1 and all("--seed" in c for c in venv1)

    plain: list = []
    g2 = SwebenchHostGrader(runner=_seed_probe_runner(plain))
    d2 = tmp_path / "plain"
    d2.mkdir()
    g2._make_venv(d2, python="3.9", seed=False)   # default
    venv2 = [c for c in plain if c[:2] == ["uv", "venv"]]
    assert venv2 and not any("--seed" in c for c in venv2)


def test_needs_seed_scoped_to_conda_base_repos() -> None:
    """Only repos in ``_CONDA_BASE_REPOS`` (currently sphinx) request ``--seed``; every
    other repo returns False, so the fix has NO blast radius on teammates' benchmarks."""
    g = SwebenchHostGrader()
    assert g._needs_seed("sphinx-doc/sphinx") is True
    assert g._needs_seed("SPHINX-DOC/SPHINX") is True   # case-insensitive
    for repo in ("django/django", "sympy/sympy", "scikit-learn/scikit-learn", "", None):
        assert g._needs_seed(repo) is False, repo


def test_era_base_pins_clamps_old_sphinx_only() -> None:
    """Old sphinx (3.x) needs era deps (setuptools<60 for pkg_resources, docutils<0.16
    for the top-level ``roman`` module); sphinx 4.x+ REQUIRE the modern deps, and other
    repos are unaffected — so the clamp must be scoped to the affected era ONLY."""
    g = SwebenchHostGrader()
    for v in ("3.0", "3.5", "3.99"):
        assert g._era_base_pins("sphinx-doc/sphinx", v) == [
            "setuptools<60", "docutils<0.16"], v
    for v in ("4.0", "4.1", "5.0", "7.2"):
        assert g._era_base_pins("sphinx-doc/sphinx", v) == [], v
    assert g._era_base_pins("django/django", "3.0") == []   # other repo: untouched
    assert g._era_base_pins("sphinx-doc/sphinx", "garbage") == []  # unparseable: no clamp


def test_install_clamps_era_deps_last(tmp_path) -> None:
    """``_install`` issues the era-pin clamp as the LAST ``uv pip install`` — after the
    spec install and the editable install — so a too-new resolve from either cannot
    survive into the test run."""
    calls: list = []

    def fake_runner(args, cwd, env=None):
        calls.append(list(args))
        return CmdResult(returncode=0)

    g = SwebenchHostGrader(runner=fake_runner)
    dest = tmp_path / "repo"
    dest.mkdir()
    spec = {"install": "python -m pip install -e .[test]"}
    g._install(dest, "PY", spec, era_pins=["setuptools<60", "docutils<0.16"])
    # The clamp must be the VERY LAST runner call overall (not merely the last
    # ``uv pip install``), so a future reorder that moved the spec install (``PY -m
    # pip ...``) or the editable install after it would fail this test.
    assert calls[-1] == ["uv", "pip", "install", "--python", "PY",
                         "setuptools<60", "docutils<0.16"]
    # ...and the editable install really does precede the clamp (it is not last).
    edit_idxs = [i for i, c in enumerate(calls)
                 if c[:3] == ["uv", "pip", "install"] and c[-2:] == ["-e", "."]]
    assert edit_idxs and max(edit_idxs) < len(calls) - 1
    # No-op when there are no era pins (newer eras / other repos).
    calls.clear()
    g._install(dest, "PY", spec, era_pins=[])
    assert all(c[-2:] != ["setuptools<60", "docutils<0.16"]
               for c in calls if c[:3] == ["uv", "pip", "install"])


def test_scm_env_is_scoped_to_pytest() -> None:
    """pytest's tagless shallow checkout needs a pretend setuptools-scm version;
    other repos keep the pre-existing empty environment overlay."""
    env = _scm_env("pytest-dev/pytest")
    assert env["SETUPTOOLS_SCM_PRETEND_VERSION"] == "9999.0.0"
    assert env["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYTEST"] == "9999.0.0"
    assert _scm_env("django/django") == {}
    assert _scm_env("") == {}


def test_install_forwards_env_overlay_to_per_task_installs(tmp_path) -> None:
    """The pytest SCM pretend version must reach both the spec install and editable
    install; otherwise pytest installs as 0.1.dev1 and its own minversion gate
    aborts every test command before collection."""
    calls: list = []

    def fake_runner(args, cwd, env=None):
        calls.append((list(args), dict(env or {})))
        return CmdResult(returncode=0)

    g = SwebenchHostGrader(runner=fake_runner)
    dest = tmp_path / "repo"
    dest.mkdir()
    env = _scm_env("pytest-dev/pytest")
    g._install(dest, "PY", {"install": "python -m pip install -e ."}, env=env)

    per_task = [
        (args, got_env) for args, got_env in calls
        if args[:2] == ["PY", "-m"] or args[:3] == ["uv", "pip", "install"]
    ]
    assert per_task
    assert all(
        got_env.get("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYTEST") == "9999.0.0"
        for _args, got_env in per_task
    )


def test_test_cmd_env_merges_scm_overlay_after_inline_env() -> None:
    """Inline env from a SWE-bench test command and the pytest SCM overlay compose
    into the subprocess env used for the test run."""
    inline, rest = _split_env_prefix(["PYTHONWARNINGS=ignore", "pytest", "-rA"])
    scm = _scm_env("pytest-dev/pytest")
    merged = {**scm, **inline}

    assert rest == ["pytest", "-rA"]
    assert merged["PYTHONWARNINGS"] == "ignore"
    assert merged["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYTEST"] == "9999.0.0"
