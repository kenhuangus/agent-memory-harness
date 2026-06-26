"""Offline tests for the grader's local-repo-mirror checkout (``MEMEVAL_REPO_CACHE``).

The grader (:class:`SwebenchHostGrader`) draws each task's checkout from github at
grade time; a single transient github timeout makes ``prepare_checkout`` raise and
silently drops the task. ``MEMEVAL_REPO_CACHE`` opts the grader into a persistent
bare mirror so per-task checkouts are network-free, with a fallback to the network
path so a cache problem never converts a gradeable task into UNGRADED.

These tests drive ``SwebenchHostGrader._checkout_repo`` over an injected stub
``GitRunner`` (and, where marked, real local-only git) — no swebench, no network,
no real github. They deliberately do NOT ``importorskip('swebench')`` (the swebench
grader suite does): ``_checkout_repo`` never touches swebench, so it is exercisable
without the optional extra installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

_THIS = Path(__file__).resolve()
if str(_THIS.parent.parent) not in sys.path:
    sys.path.insert(0, str(_THIS.parent.parent))

from memeval.claudecode.checkout import GitResult  # noqa: E402
from memeval.grader_swebench import SwebenchHostGrader  # noqa: E402
from memeval.schema import Benchmark, Task, TaskKind  # noqa: E402

_GRADER_LOGGER = "memeval.grader_swebench"


def _task(repo: str = "pydata/xarray", base: str = "0" * 40) -> Task:
    return Task(
        task_id="pydata__xarray-1000",
        benchmark=Benchmark.SWE_BENCH_CL,
        kind=TaskKind.CODE,
        question="fix it",
        repo=repo,
        base_commit=base,
    )


class _EnvCache:
    """Context manager: set/unset ``MEMEVAL_REPO_CACHE`` and restore on exit."""

    def __init__(self, value: Optional[str]) -> None:
        self.value = value

    def __enter__(self) -> "_EnvCache":
        self._old = os.environ.get("MEMEVAL_REPO_CACHE")
        if self.value is None:
            os.environ.pop("MEMEVAL_REPO_CACHE", None)
        else:
            os.environ["MEMEVAL_REPO_CACHE"] = self.value
        return self

    def __exit__(self, *exc) -> None:
        if self._old is None:
            os.environ.pop("MEMEVAL_REPO_CACHE", None)
        else:
            os.environ["MEMEVAL_REPO_CACHE"] = self._old


def _materializing(cwd: Path) -> None:
    """A stub git step's side effect: write a source file into the checkout dir."""
    cwd.mkdir(parents=True, exist_ok=True)
    (cwd / "src.py").write_text("x\n", encoding="utf-8")


class CheckoutRepoEnvKnob(unittest.TestCase):
    def test_default_unset_uses_auto_and_attempts_no_mirror(self) -> None:
        """``MEMEVAL_REPO_CACHE`` unset -> the exact historical auto sequence, and NO
        mirror clone is attempted (byte-identical to today)."""
        calls: list = []

        def git(args, cwd, *a, **kw) -> GitResult:
            calls.append(list(args))
            if args and args[0] in ("init", "remote", "fetch", "checkout"):
                _materializing(Path(cwd))
            return GitResult(returncode=0)

        g = SwebenchHostGrader(git_runner=git)
        with _EnvCache(None), tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "repo"
            g._checkout_repo(dest, _task())
            self.assertTrue((dest / "src.py").exists())
        ops = [c[0] for c in calls if c]
        self.assertEqual(ops, ["init", "remote", "fetch", "checkout"])
        self.assertFalse(any(c[:2] == ["clone", "--mirror"] for c in calls))
        self.assertFalse(any(c[:2] == ["clone", "--shared"] for c in calls))

    def test_warm_mirror_uses_local_with_zero_network(self) -> None:
        """A warm mirror -> ``clone --shared`` local checkout, with NO github network
        (no ``clone --mirror``, no ``fetch``)."""
        calls: list = []

        def git(args, cwd, *a, **kw) -> GitResult:
            calls.append(list(args))
            if args[:2] == ["clone", "--shared"]:
                _materializing(Path(cwd))
            return GitResult(returncode=0)

        g = SwebenchHostGrader(git_runner=git)
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            mirror = cache / "pydata__xarray.git"
            mirror.mkdir(parents=True)
            (mirror / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            dest = Path(tmp) / "repo"
            with _EnvCache(str(cache)):
                g._checkout_repo(dest, _task())
            self.assertTrue((dest / "src.py").exists())
        self.assertTrue(any(c[:2] == ["clone", "--shared"] for c in calls))
        self.assertFalse(any(c[:2] == ["clone", "--mirror"] for c in calls))  # no net
        self.assertFalse(any(c and c[0] == "fetch" for c in calls))           # no net

    def test_mirror_auto_created_once_when_absent(self) -> None:
        """Absent mirror -> ``clone --mirror`` ONCE, then a local checkout from it."""
        calls: list = []

        def git(args, cwd, *a, **kw) -> GitResult:
            calls.append(list(args))
            if args[:2] == ["clone", "--mirror"]:
                target = Path(args[-1])
                target.mkdir(parents=True, exist_ok=True)
                (target / "HEAD").write_text("ref\n", encoding="utf-8")
            elif args[:2] == ["clone", "--shared"]:
                _materializing(Path(cwd))
            return GitResult(returncode=0)

        g = SwebenchHostGrader(git_runner=git)
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            dest = Path(tmp) / "repo"
            with _EnvCache(str(cache)):
                g._checkout_repo(dest, _task())
            self.assertTrue((dest / "src.py").exists())
        self.assertEqual(
            sum(1 for c in calls if c[:2] == ["clone", "--mirror"]), 1)

    def test_local_failure_falls_back_to_auto_and_warns(self) -> None:
        """A failing local checkout (stale/missing base_commit) updates the mirror,
        retries local, then FALLS BACK to the network auto path — and a cache problem
        never UNGRADES the task. A WARNING is logged on the miss."""
        calls: list = []

        def git(args, cwd, *a, **kw) -> GitResult:
            calls.append(list(args))
            if args[:2] == ["clone", "--mirror"]:
                target = Path(args[-1])
                target.mkdir(parents=True, exist_ok=True)
                (target / "HEAD").write_text("ref\n", encoding="utf-8")
                return GitResult(returncode=0)
            if args[:2] == ["clone", "--shared"]:
                return GitResult(returncode=1, stderr="fatal: bad object <sha>")
            if args[:2] == ["remote", "update"]:
                return GitResult(returncode=0)
            if args and args[0] in ("init", "remote", "fetch", "checkout"):
                _materializing(Path(cwd))
            return GitResult(returncode=0)

        g = SwebenchHostGrader(git_runner=git)
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            dest = Path(tmp) / "repo"
            with _EnvCache(str(cache)), \
                    self.assertLogs(_GRADER_LOGGER, level="WARNING") as cm:
                g._checkout_repo(dest, _task())
            self.assertTrue((dest / "src.py").exists())  # auto fallback materialized
        # Local tried twice (first + after the mirror update), then auto ran.
        self.assertEqual(
            sum(1 for c in calls if c[:2] == ["clone", "--shared"]), 2)
        self.assertTrue(any(c[:2] == ["remote", "update"] for c in calls))
        self.assertTrue(any(c == ["init"] for c in calls))  # auto fallback engaged
        self.assertTrue(any("falling back to network" in m for m in cm.output))


def _have_git() -> bool:
    return shutil.which("git") is not None


def _git_env() -> dict:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
    }


@unittest.skipUnless(_have_git(), "git not available")
class CheckoutRepoRealGitWarmMirror(unittest.TestCase):
    """End-to-end with REAL local-only git: a warm mirror grades a base_commit with
    zero github (the source + mirror are on local disk; github is never contacted)."""

    def _git(self, *args: str, cwd: Path) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                              capture_output=True, text=True, env=_git_env())

    def test_grader_checks_out_base_from_warm_mirror(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "src"
            src.mkdir()
            self._git("init", "-q", cwd=src)
            (src / "f.txt").write_text("base\n", encoding="utf-8")
            self._git("add", "f.txt", cwd=src)
            self._git("commit", "-qm", "base", cwd=src)
            base = self._git("rev-parse", "HEAD", cwd=src).stdout.strip()
            (src / "f.txt").write_text("head\n", encoding="utf-8")
            self._git("add", "f.txt", cwd=src)
            self._git("commit", "-qm", "head", cwd=src)

            cache = tmp / "cache"
            cache.mkdir()
            self._git("clone", "--mirror", str(src),
                      str(cache / "pydata__xarray.git"), cwd=tmp)

            g = SwebenchHostGrader()  # real subprocess git runner
            dest = tmp / "co"
            with _EnvCache(str(cache)):
                g._checkout_repo(dest, _task(base=base))
            self.assertEqual((dest / "f.txt").read_text(encoding="utf-8"), "base\n")


if __name__ == "__main__":
    unittest.main()
