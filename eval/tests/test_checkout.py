"""Unit tests for the git checkout seam (memeval.claudecode.checkout).

Stdlib-only: no network, no real git, no real `claude`. Every git op goes through
an injected fake runner that synthesizes the checkout on disk itself (mirroring the
fake in test_claudecode_code_agent.py). The focus here is the *rerun robustness*
fix: `prepare_checkout` must be idempotent when the dest persists across runs
(the `--out-dir` stable-working-dir case), while the offline first-run path — where
the injected runner writes into a dest with no real `.git` — stays unchanged.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Make the package importable when run directly (mirrors test_sandbox.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memeval.claudecode.checkout import (  # noqa: E402
    CheckoutError,
    GitResult,
    _retry,
    _subprocess_git,
    ensure_mirror,
    mirror_path_for,
    prepare_checkout,
)


def _make_fake_git(*, real_git_dir: bool, calls: list | None = None):
    """A fake GitRunner that synthesizes a checkout on disk — no real git, no network.

    ``real_git_dir`` controls whether ``init`` materializes a real ``.git`` directory:

    * ``True`` mimics real git (and the stable-working-dir rerun case): a second
      ``prepare_checkout`` into the same dest finds ``.git`` and, WITHOUT the fix, the
      fake ``remote add origin`` below would reject the duplicate.
    * ``False`` mimics the offline stub path used by the agent tests: the runner writes
      a file into ``dest`` but never creates a real ``.git``, so the idempotency wipe
      must NOT trigger.

    ``calls`` (a list) records every ``(op, args)`` so tests can assert the sequence.
    """
    def _fake_git(args, cwd, *a, **kw) -> GitResult:
        cwd = Path(cwd)
        op = args[0] if args else ""
        if calls is not None:
            calls.append((op, list(args)))
        if op == "init":
            cwd.mkdir(parents=True, exist_ok=True)
            if real_git_dir:
                (cwd / ".git").mkdir(exist_ok=True)
            return GitResult(returncode=0)
        if op == "remote":
            # `remote add origin <url>` — real git rejects a duplicate (rc=3). State is
            # tracked ON DISK (a marker inside .git) rather than in a closure, so the
            # production fix's rmtree of a stale .git genuinely clears it — exactly as
            # real git would. Pre-fix, the marker persists and the rerun fails (rc=3).
            if len(args) >= 2 and args[1] == "add":
                marker = cwd / ".git" / "origin-added"
                if marker.exists():
                    return GitResult(returncode=3,
                                     stderr="error: remote origin already exists.")
                if (cwd / ".git").is_dir():
                    marker.write_text("1", encoding="utf-8")
            return GitResult(returncode=0)
        if op in ("fetch", "checkout", "clone"):
            cwd.mkdir(parents=True, exist_ok=True)
            (cwd / "orm.py").write_text("def filter_empty():\n    return None\n",
                                        encoding="utf-8")
            return GitResult(returncode=0)
        return GitResult(returncode=0)

    return _fake_git


class PrepareCheckoutFirstRun(unittest.TestCase):
    """The offline / first-run path (no pre-existing real .git) is unchanged."""

    def test_first_run_succeeds_and_materializes_checkout(self) -> None:
        calls: list = []
        git = _make_fake_git(real_git_dir=False, calls=calls)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "repo"
            out = prepare_checkout("example/django-fork", "aaaa1111", dest, git_runner=git)
            self.assertEqual(out, dest.resolve())
            self.assertTrue((dest / "orm.py").exists())  # checkout materialized
        ops = [op for op, _ in calls]
        self.assertEqual(ops, ["init", "remote", "fetch", "checkout"])

    def test_first_run_offline_stub_dest_not_wiped(self) -> None:
        # Offline stub: the injected runner writes a file into dest BEFORE the git ops
        # (as the agent fixture's seeded store would), and dest has no real .git. The
        # idempotency wipe must not fire and clobber it.
        git = _make_fake_git(real_git_dir=False)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "repo"
            dest.mkdir(parents=True)
            (dest / "pre-seeded.txt").write_text("keep me", encoding="utf-8")
            prepare_checkout("example/repo", "bbbb2222", dest, git_runner=git)
            self.assertTrue((dest / "pre-seeded.txt").exists())  # survived (no .git wipe)


class PrepareCheckoutRerunIdempotent(unittest.TestCase):
    """The fix: a reused dest with a populated .git succeeds on every run."""

    def test_second_run_into_same_dest_succeeds(self) -> None:
        git = _make_fake_git(real_git_dir=True)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "repo"
            # Run 1 — first checkout (leaves a real .git behind, as production does).
            prepare_checkout("example/repo", "aaaa1111", dest, git_runner=git)
            self.assertTrue((dest / ".git").exists())
            # Run 2 — same dest. Pre-fix this raised CheckoutError on `remote add origin`
            # ("remote origin already exists", rc=3). With the wipe it succeeds.
            out = prepare_checkout("example/repo", "aaaa1111", dest, git_runner=git)
            self.assertEqual(out, dest.resolve())
            self.assertTrue((dest / "orm.py").exists())

    def test_rerun_wipe_clears_stale_tree(self) -> None:
        # The wipe also clears a stale working tree (e.g. last run's edits / index),
        # so each run starts clean.
        git = _make_fake_git(real_git_dir=True)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "repo"
            prepare_checkout("example/repo", "aaaa1111", dest, git_runner=git)
            stale = dest / "stale-from-last-run.txt"
            stale.write_text("garbage", encoding="utf-8")
            prepare_checkout("example/repo", "aaaa1111", dest, git_runner=git)
            self.assertFalse(stale.exists())   # wiped
            self.assertTrue((dest / "orm.py").exists())  # fresh checkout present

    def test_third_run_also_succeeds(self) -> None:
        # Idempotency is not a one-shot: many reruns in a row all work.
        git = _make_fake_git(real_git_dir=True)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "repo"
            for _ in range(3):
                prepare_checkout("example/repo", "aaaa1111", dest, git_runner=git)
            self.assertTrue((dest / "orm.py").exists())


class PrepareCheckoutErrorStillRaises(unittest.TestCase):
    """A genuine git failure (not the duplicate-remote case) still surfaces."""

    def test_init_failure_raises_checkout_error(self) -> None:
        def failing_git(args, cwd, *a, **kw) -> GitResult:
            if args and args[0] == "init":
                return GitResult(returncode=1, stderr="permission denied")
            return GitResult(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "repo"
            with self.assertRaises(CheckoutError):
                prepare_checkout("example/repo", "aaaa1111", dest, git_runner=failing_git)


# --------------------------------------------------------------------------- #
# Retry-with-backoff helper (defense-in-depth for a transient github blip).
# --------------------------------------------------------------------------- #
class RetryHelper(unittest.TestCase):
    """``_retry`` retries CheckoutError with exponential backoff; sleep is injected."""

    def test_succeeds_after_transient_failures(self) -> None:
        slept: list = []
        state = {"n": 0}

        def flaky() -> str:
            state["n"] += 1
            if state["n"] < 3:
                raise CheckoutError("connection timed out")
            return "ok"

        out = _retry(flaky, attempts=3, sleep=slept.append)
        self.assertEqual(out, "ok")
        self.assertEqual(state["n"], 3)
        self.assertEqual(slept, [1.0, 2.0])  # backoff BETWEEN the 2 failures only

    def test_raises_after_exhausting_attempts(self) -> None:
        slept: list = []

        def always() -> str:
            raise CheckoutError("down")

        with self.assertRaises(CheckoutError):
            _retry(always, attempts=3, sleep=slept.append)
        self.assertEqual(len(slept), 2)  # slept between attempts, never after the last

    def test_single_attempt_never_sleeps(self) -> None:
        slept: list = []

        def always() -> str:
            raise CheckoutError("down")

        with self.assertRaises(CheckoutError):
            _retry(always, attempts=1, sleep=slept.append)
        self.assertEqual(slept, [])  # one attempt -> no backoff at all


# --------------------------------------------------------------------------- #
# Repo -> mirror-path mapping: short form and URL forms share one mirror.
# --------------------------------------------------------------------------- #
class MirrorPathMapping(unittest.TestCase):
    def test_short_and_url_forms_map_to_same_mirror(self) -> None:
        cache = Path("/tmp/cache")
        expected = cache / "pydata__xarray.git"
        for repo in (
            "pydata/xarray",
            "https://github.com/pydata/xarray",
            "https://github.com/pydata/xarray.git",
            "http://github.com/pydata/xarray.git",
            "git@github.com:pydata/xarray.git",
            "ssh://git@github.com/pydata/xarray.git",
        ):
            self.assertEqual(mirror_path_for(repo, cache), expected, repo)

    def test_unparseable_repo_raises(self) -> None:
        with self.assertRaises(CheckoutError):
            mirror_path_for("noslash", "/tmp/cache")


# --------------------------------------------------------------------------- #
# prepare_checkout: the network fetch is retried (defense-in-depth).
# --------------------------------------------------------------------------- #
class PrepareCheckoutFetchRetry(unittest.TestCase):
    def test_fetch_retried_then_succeeds(self) -> None:
        slept: list = []
        state = {"fetch": 0}

        def git(args, cwd, *a, **kw) -> GitResult:
            cwd = Path(cwd)
            op = args[0] if args else ""
            if op == "fetch":
                state["fetch"] += 1
                if state["fetch"] < 2:
                    return GitResult(returncode=1, stderr="connection timed out")
                cwd.mkdir(parents=True, exist_ok=True)
                (cwd / "f.py").write_text("x", encoding="utf-8")
            return GitResult(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "repo"
            prepare_checkout("o/n", "sha", dest, git_runner=git,
                             retries=3, sleep=slept.append)
            self.assertEqual(state["fetch"], 2)   # failed once, succeeded on retry
            self.assertEqual(slept, [1.0])        # one backoff before the retry

    def test_default_retries_one_raises_immediately(self) -> None:
        # Default retries=1 = historical behavior: a fetch failure raises at once,
        # no sleep, no retry (byte-identical to before this change).
        slept: list = []

        def git(args, cwd, *a, **kw) -> GitResult:
            if args and args[0] == "fetch":
                return GitResult(returncode=1, stderr="timeout")
            return GitResult(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "repo"
            with self.assertRaises(CheckoutError):
                prepare_checkout("o/n", "sha", dest, git_runner=git, sleep=slept.append)
            self.assertEqual(slept, [])


# --------------------------------------------------------------------------- #
# ensure_mirror: clone --mirror ONCE when absent, then reuse with zero network.
# --------------------------------------------------------------------------- #
class EnsureMirrorAutoCreate(unittest.TestCase):
    def test_creates_once_then_reuses(self) -> None:
        calls: list = []

        def git(args, cwd, *a, **kw) -> GitResult:
            calls.append(list(args))
            if args[:2] == ["clone", "--mirror"]:
                target = Path(args[-1])           # clones into a temp path
                target.mkdir(parents=True, exist_ok=True)
                (target / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            return GitResult(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            m1 = ensure_mirror("pydata/xarray", cache, git_runner=git)
            self.assertEqual(m1, cache / "pydata__xarray.git")
            self.assertTrue((m1 / "HEAD").exists())  # atomically moved into place
            clones = [c for c in calls if c[:2] == ["clone", "--mirror"]]
            self.assertEqual(len(clones), 1)

            # Second call: warm mirror -> no new clone (zero network).
            m2 = ensure_mirror("pydata/xarray", cache, git_runner=git)
            self.assertEqual(m2, m1)
            clones = [c for c in calls if c[:2] == ["clone", "--mirror"]]
            self.assertEqual(len(clones), 1)


# --------------------------------------------------------------------------- #
# Local checkout from a REAL on-disk bare mirror — no network, no github.
# --------------------------------------------------------------------------- #
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
class LocalCheckoutFromRealMirror(unittest.TestCase):
    """A warm bare mirror + the ``local`` strategy check out a base_commit offline."""

    def _git(self, *args: str, cwd: Path) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                              capture_output=True, text=True, env=_git_env())

    def test_warm_mirror_local_checkout_of_base_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "src"
            src.mkdir()
            self._git("init", "-q", cwd=src)
            (src / "f.txt").write_text("base-content\n", encoding="utf-8")
            self._git("add", "f.txt", cwd=src)
            self._git("commit", "-qm", "base", cwd=src)
            base = self._git("rev-parse", "HEAD", cwd=src).stdout.strip()
            (src / "f.txt").write_text("head-content\n", encoding="utf-8")
            self._git("add", "f.txt", cwd=src)
            self._git("commit", "-qm", "head", cwd=src)

            # Pre-warm a bare mirror at the path ensure_mirror derives for this repo.
            cache = tmp / "cache"
            cache.mkdir()
            mirror_target = cache / "pydata__xarray.git"
            self._git("clone", "--mirror", str(src), str(mirror_target), cwd=tmp)

            # ensure_mirror sees the warm mirror -> ZERO network: the runner (which
            # would error) is never invoked.
            net_calls: list = []

            def tracking_runner(args, cwd, *a, **kw) -> GitResult:
                net_calls.append(list(args))
                return GitResult(returncode=1, stderr="should not be called")

            mirror = ensure_mirror("pydata/xarray", cache, git_runner=tracking_runner)
            self.assertEqual(mirror, mirror_target)
            self.assertEqual(net_calls, [])   # no clone/fetch: nothing hit the network

            # Local checkout of the BASE commit from the mirror (real git, no network).
            dest = tmp / "co"
            prepare_checkout(str(mirror), base, dest, strategy="local")
            self.assertEqual((dest / "f.txt").read_text(encoding="utf-8"),
                             "base-content\n")  # base, not head


class SubprocessGitErrorNormalization(unittest.TestCase):
    """The real ``_subprocess_git`` converts subprocess-level failures into a non-zero
    ``GitResult`` (which ``_run`` turns into ``CheckoutError`` so ``_retry`` can RETRY),
    instead of letting a raw exception escape the retry (which catches only
    ``CheckoutError``) and drop a task on the first transient blip."""

    def test_timeout_becomes_nonzero_result_not_raise(self) -> None:
        def boom(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="git fetch", timeout=600)

        with mock.patch.object(subprocess, "run", boom):
            res = _subprocess_git(["fetch", "--depth", "1", "origin", "deadbeef"],
                                  Path("."), timeout=600)
        self.assertNotEqual(res.returncode, 0)   # retryable, not a raised exception
        self.assertIn("timed out", res.stderr)

    def test_oserror_becomes_nonzero_result_not_raise(self) -> None:
        def boom(*a, **kw):
            raise FileNotFoundError("git not found")

        with mock.patch.object(subprocess, "run", boom):
            res = _subprocess_git(["fetch"], Path("."))
        self.assertNotEqual(res.returncode, 0)   # surfaced as a result, not a crash

    def test_subprocess_timeout_is_retried_via_prepare_checkout(self) -> None:
        """End-to-end: a fetch that raises TimeoutExpired twice (normalized to a
        non-zero result) is retried and then succeeds — proving the gap CodeRabbit
        flagged is closed (the raw timeout no longer escapes _retry)."""
        attempts = {"fetch": 0}

        def flaky(args, cwd, *a, **kw) -> GitResult:
            if args[:1] == ["fetch"]:
                attempts["fetch"] += 1
                if attempts["fetch"] < 3:
                    # Mimic _subprocess_git's normalized timeout result.
                    return GitResult(returncode=124, stderr="git fetch timed out after 600s")
                Path(cwd).mkdir(parents=True, exist_ok=True)
                (Path(cwd) / "src.py").write_text("x\n", encoding="utf-8")
            return GitResult(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "repo"
            prepare_checkout("pydata/xarray", "0" * 40, dest, strategy="auto",
                             git_runner=flaky, retries=3, sleep=lambda _s: None)
            self.assertTrue((dest / "src.py").exists())
        self.assertEqual(attempts["fetch"], 3)   # retried twice, succeeded on the third


if __name__ == "__main__":
    unittest.main()
