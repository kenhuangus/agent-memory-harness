# Copyright contributors to the agent-memory-harness project.
#
# Regression test for the --plugin-workers N>1 bundle race.
#
# Under --plugin-workers N>1 the worker threads share ONE ClaudeCodeAgent, so they
# all reach _ensure_real_plugin() concurrently with _real_plugin_env=None. Before the
# fix this was an unsynchronized check-then-set: every worker called
# sandbox.setup_real_plugin() against the SAME shared _plugin-bundle dir, which
# rmtree+copytree's the bundle and re-adds the marketplace -> one worker wipes/rebuilds
# the dir while another reads it -> "Directory not empty" / missing marketplace.json
# (19/20 VISTA dev-20 journeys ERROR'd in the smoke).
#
# The fix is a harness-side build-once double-checked threading.Lock in
# _ensure_real_plugin (eval/memeval/claudecode/agent.py). This test spins up several
# threads hitting _ensure_real_plugin() at once and asserts setup_real_plugin is
# invoked EXACTLY ONCE and every thread gets the same cached env back, with no
# exception. Deterministic: no network, no real claude CLI -- the real install is
# replaced by a counting/sleeping spy. The spy's sleep widens the race window so an
# unsynchronized implementation would reliably call it more than once.
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

# Make the package importable when run directly.
_THIS = Path(__file__).resolve()
_BASE_DIR = _THIS.parent.parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from memeval.claudecode import agent as A  # noqa: E402
from memeval.claudecode.agent import ClaudeCodeAgent  # noqa: E402
from memeval.claudecode.platform import ClaudeRuntime  # noqa: E402

_NATIVE = ClaudeRuntime(kind="native", exe="claude", python="python")

N_THREADS = 8


def _make_agent() -> ClaudeCodeAgent:
    agent = ClaudeCodeAgent(memory_mode="plugin-real", runner=A.run_claude,
                            runtime=_NATIVE)
    # Force the real-install branch: _ensure_real_plugin only calls
    # sandbox.setup_real_plugin when self._runner is run_claude. Constructing with
    # runner=A.run_claude already satisfies that; assert it so the test stays honest
    # if the constructor default ever changes.
    assert agent._runner is A.run_claude
    return agent


class PluginWorkersRaceTest(unittest.TestCase):
    def test_ensure_real_plugin_builds_exactly_once_under_concurrency(self) -> None:
        agent = _make_agent()

        calls = []
        calls_lock = threading.Lock()
        sentinel_env = {"PATH": "/fake/plugin/bin"}

        def _spy_setup_real_plugin(*, claude_exe=None):
            # Record + simulate the slow rmtree/copytree/marketplace work so an
            # unsynchronized check-then-set would let other threads in mid-build.
            with calls_lock:
                calls.append(claude_exe)
            time.sleep(0.05)
            return dict(sentinel_env)

        orig = A.sandbox.setup_real_plugin
        A.sandbox.setup_real_plugin = _spy_setup_real_plugin
        try:
            results: list = [None] * N_THREADS
            errors: list = [None] * N_THREADS
            barrier = threading.Barrier(N_THREADS)

            def _worker(i: int) -> None:
                try:
                    barrier.wait()  # release all threads simultaneously
                    results[i] = agent._ensure_real_plugin()
                except Exception as exc:  # pragma: no cover - failure path
                    errors[i] = exc

            threads = [threading.Thread(target=_worker, args=(i,))
                       for i in range(N_THREADS)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
        finally:
            A.sandbox.setup_real_plugin = orig

        # No worker raised.
        self.assertEqual([e for e in errors if e is not None], [],
                         f"workers raised: {errors}")
        # The expensive build+install ran EXACTLY once despite N concurrent callers.
        self.assertEqual(len(calls), 1, f"setup_real_plugin called {len(calls)}x: {calls}")
        # Every worker got the SAME cached env object back.
        self.assertTrue(all(r is agent._real_plugin_env for r in results),
                        "workers did not all receive the cached env")
        self.assertEqual(agent._real_plugin_env, sentinel_env)

    def test_offline_runner_skips_install_and_is_thread_safe(self) -> None:
        # With a fake (non-run_claude) runner, the install is skipped and the cache is {}.
        agent = ClaudeCodeAgent(memory_mode="plugin-real",
                                runner=lambda *a, **k: None, runtime=_NATIVE)

        def _boom(*a, **k):  # must never be called on the offline path
            raise AssertionError("setup_real_plugin must not run under a fake runner")

        orig = A.sandbox.setup_real_plugin
        A.sandbox.setup_real_plugin = _boom
        try:
            results: list = [None] * N_THREADS
            barrier = threading.Barrier(N_THREADS)

            def _worker(i: int) -> None:
                barrier.wait()
                results[i] = agent._ensure_real_plugin()

            threads = [threading.Thread(target=_worker, args=(i,))
                       for i in range(N_THREADS)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
        finally:
            A.sandbox.setup_real_plugin = orig

        self.assertEqual(agent._real_plugin_env, {})
        self.assertTrue(all(r == {} for r in results))


if __name__ == "__main__":
    unittest.main()
