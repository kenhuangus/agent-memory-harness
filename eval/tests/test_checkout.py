"""Unit tests for the git checkout seam (memeval.claudecode.checkout).

Stdlib-only: no network, no real git, no real `claude`. Every git op goes through
an injected fake runner that synthesizes the checkout on disk itself (mirroring the
fake in test_claudecode_code_agent.py). The focus here is the *rerun robustness*
fix: `prepare_checkout` must be idempotent when the dest persists across runs
(the `--out-dir` stable-working-dir case), while the offline first-run path — where
the injected runner writes into a dest with no real `.git` — stays unchanged.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Make the package importable when run directly (mirrors test_sandbox.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memeval.claudecode.checkout import (  # noqa: E402
    CheckoutError,
    GitResult,
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


if __name__ == "__main__":
    unittest.main()
