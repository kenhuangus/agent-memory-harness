"""Offline tests for Fix A — the plugin-real CROSS-TASK group store.

These prove, deterministically and with no network / no real ``claude`` / no real
``daydream-cli``, that the plugin's own learning ACCUMULATES across the tasks of a
SWE-Bench-CL sequence (tasks sharing a ``group_id``):

* the same per-group store dir is used for every task in the group;
* memory written into task 1's ``.cookbook-memory`` (a stand-in for a real daydream
  write) is copied IN for task 2 (so task N+1 starts from what task N learned);
* loader priors are seeded exactly once per group (not re-seeded per task);
* a task with ``group_id=None`` keeps the per-task path — no group store is created;
* ``events.jsonl`` is NEVER copied between tasks (per-task recall attribution stays
  intact), and the copy helper excludes it.

The real daydream drain + the headless turn are behind the injected runner (the
drain no-ops under a fake runner — :meth:`ClaudeCodeAgent._drain_daydream` gates on
``self._runner is run_claude``), so the COPY + SEED-ONCE + PATH logic is what these
tests exercise. The fake runner stands in for the installed plugin: it writes a
marker "memory" file (and a recall event) into the per-task store, exactly where a
real daydream write would land.

Run under the swebench venv with PYTHONPATH=. from eval/.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

# Make the package importable when run directly.
_THIS = Path(__file__).resolve()
_BASE_DIR = _THIS.parent.parent
import sys

if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from memeval.claudecode import agent as A  # noqa: E402
from memeval.claudecode.agent import (  # noqa: E402
    ClaudeCodeAgent,
    _copy_store_contents,
    _GROUP_STORE_EXCLUDE,
)
from memeval.claudecode.cli import ClaudeResult  # noqa: E402
from memeval.claudecode.platform import ClaudeRuntime  # noqa: E402
from memeval.schema import Benchmark, Session, Task, TaskKind  # noqa: E402

SkipTest = unittest.SkipTest
_NATIVE = ClaudeRuntime(kind="native", exe="claude", python="python")


# --------------------------------------------------------------------------- #
# Minimal AgentContext stand-in (record_* are no-ops we don't assert on here).
# --------------------------------------------------------------------------- #
class _Ctx:
    def record_generate(self, *a, **k) -> None: ...
    def record_retrieve(self, *a, **k) -> None: ...
    def note(self, *a, **k) -> None: ...


def _qa_task(task_id: str, *, group_id=None, order=0, sessions=None) -> Task:
    return Task(
        task_id=task_id, benchmark=Benchmark.SWE_BENCH_CL, kind=TaskKind.QA,
        question=f"q-{task_id}", group_id=group_id, order=order,
        sessions=list(sessions or []),
    )


def _read_events(store: Path) -> list[dict]:
    ev = store / "events.jsonl"
    if not ev.exists():
        return []
    return [json.loads(line) for line in ev.read_text().splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# Fake runner: stands in for the installed plugin. On each turn it writes a marker
# "memory" file (named per the task so we can prove cross-task carry) plus a recall
# event, into the per-task store under ${CLAUDE_PROJECT_DIR}/.cookbook-memory.
# --------------------------------------------------------------------------- #
def _make_fake_plugin_runner(turns: dict):
    """``turns`` accumulates the per-turn observation list. The runner records, for
    each turn, which marker files the per-task store ALREADY contains when the turn
    starts (i.e. what was copied in from the group store)."""

    def fake(prompt, *, cwd, extra_env=None, **kw) -> ClaudeResult:
        store = Path((extra_env or {}).get("CLAUDE_PROJECT_DIR", cwd)) / ".cookbook-memory"
        store.mkdir(parents=True, exist_ok=True)
        # Observe what memory was restored INTO this task's store before the turn ran.
        markers_present = sorted(p.name for p in store.glob("mem_*.md"))
        turns.setdefault("seen", []).append(markers_present)
        # Simulate the plugin's daydream WRITE: a new memory file for this task.
        task_marker = (prompt.split("q-")[-1].split()[0] if "q-" in prompt else "x")
        (store / f"mem_{task_marker}.md").write_text(f"learned in {task_marker}\n",
                                                     encoding="utf-8")
        # Simulate the plugin's recall event in its OWN per-task events stream.
        ev = {"ts": 1.0, "op": "recall", "ids": ["m1"], "query": prompt,
              "meta": {"hits": [{"id": "m1", "content": "x", "score": 0.9, "rank": 0,
                                 "tokens": 1, "timestamp": 1.0}]}}
        with open(store / "events.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev) + "\n")
        return ClaudeResult(text="done", tokens_in=5, tokens_out=1)

    return fake


# --------------------------------------------------------------------------- #
# Copy helper — unit coverage (excludes events.jsonl).
# --------------------------------------------------------------------------- #
def test_copy_store_contents_excludes_events_jsonl() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        dst = Path(tmp) / "dst"
        src.mkdir()
        (src / "mem_a.md").write_text("a", encoding="utf-8")
        (src / "events.jsonl").write_text('{"op":"recall"}\n', encoding="utf-8")
        sub = src / "markdown" / "memory"
        sub.mkdir(parents=True)
        (sub / "x.md").write_text("x", encoding="utf-8")

        _copy_store_contents(src, dst)

        assert (dst / "mem_a.md").exists()                       # memory copied
        assert (dst / "markdown" / "memory" / "x.md").exists()   # nested dirs merged
        assert not (dst / "events.jsonl").exists()               # events EXCLUDED
        assert "events.jsonl" in _GROUP_STORE_EXCLUDE


def test_copy_store_contents_missing_src_is_noop() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "dst"
        _copy_store_contents(Path(tmp) / "nope", dst)   # missing src
        assert not dst.exists() or not any(dst.iterdir())


# --------------------------------------------------------------------------- #
# Two grouped tasks share ONE group store; task 1's memory is present for task 2.
# --------------------------------------------------------------------------- #
def test_group_store_carries_memory_across_tasks() -> None:
    turns: dict = {}
    runner = _make_fake_plugin_runner(turns)
    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="plugin-real", runner=runner,
                                runtime=_NATIVE, workdir=tmp)
        t1 = _qa_task("cl_1", group_id="seqA", order=0)
        t2 = _qa_task("cl_2", group_id="seqA", order=1)
        agent.solve(t1, _Ctx())
        agent.solve(t2, _Ctx())

        # The SAME group store dir is keyed on group_id.
        gs1 = agent._plugin_group_store(t1)
        gs2 = agent._plugin_group_store(t2)
        assert gs1 == gs2 and gs1 is not None
        assert gs1.is_dir()

        # Task 1 saw an empty store (first task); task 2 saw task 1's memory copied IN.
        seen = turns["seen"]
        assert seen[0] == []                       # task 1: nothing restored
        assert "mem_cl_1.md" in seen[1]            # task 2: task 1's memory restored

        # The group store accumulated BOTH tasks' memory.
        names = sorted(p.name for p in gs1.glob("mem_*.md"))
        assert names == ["mem_cl_1.md", "mem_cl_2.md"]


# --------------------------------------------------------------------------- #
# events.jsonl is per-task: NOT copied between tasks, NOT in the group store.
# --------------------------------------------------------------------------- #
def test_events_jsonl_stays_per_task() -> None:
    turns: dict = {}
    runner = _make_fake_plugin_runner(turns)
    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="plugin-real", runner=runner,
                                runtime=_NATIVE, workdir=tmp)
        t1 = _qa_task("cl_1", group_id="seqB", order=0)
        t2 = _qa_task("cl_2", group_id="seqB", order=1)
        agent.solve(t1, _Ctx())
        agent.solve(t2, _Ctx())

        store1 = agent._task_dir(t1) / ".cookbook-memory"
        store2 = agent._task_dir(t2) / ".cookbook-memory"
        gs = agent._plugin_group_store(t1)

        # Each per-task store has exactly its OWN one recall event (not accumulated).
        assert len(_read_events(store1)) == 1
        assert len(_read_events(store2)) == 1
        # The group store never holds events.jsonl.
        assert not (gs / "events.jsonl").exists()
        # task 2's store did NOT inherit task 1's events (recall attribution intact):
        # its single event references its own prompt.
        assert _read_events(store2)[0]["query"].endswith("cl_2") or "cl_2" in \
            _read_events(store2)[0]["query"]


# --------------------------------------------------------------------------- #
# Loader priors are seeded ONCE per group (task 1), not re-seeded for task 2.
# --------------------------------------------------------------------------- #
def test_seed_priors_once_per_group() -> None:
    turns: dict = {}
    runner = _make_fake_plugin_runner(turns)
    seed_calls: list = []
    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="plugin-real", runner=runner,
                                runtime=_NATIVE, workdir=tmp)
        # Spy on the seed path (the real user/Daydreamer write surface).
        orig_seed = agent._seed_plugin_store

        def _spy(task, store_dir, plugin_env):
            seed_calls.append(task.task_id)
            return orig_seed(task, store_dir, plugin_env)

        agent._seed_plugin_store = _spy  # type: ignore[assignment]

        s = [Session(session_id="s0", content="prior fact", timestamp=0.0)]
        t1 = _qa_task("cl_1", group_id="seqC", order=0, sessions=s)
        t2 = _qa_task("cl_2", group_id="seqC", order=1, sessions=s)
        agent.solve(t1, _Ctx())
        agent.solve(t2, _Ctx())

        # Seeded exactly once — for the first task of the group only.
        assert seed_calls == ["cl_1"]
        # The seed sentinel marks the group as seeded.
        gs = agent._plugin_group_store(t1)
        assert (gs / A._GROUP_SEEDED_SENTINEL).exists()


# --------------------------------------------------------------------------- #
# group_id=None -> per-task path; NO group store created (no behavior change).
# --------------------------------------------------------------------------- #
def test_ungrouped_task_creates_no_group_store() -> None:
    turns: dict = {}
    runner = _make_fake_plugin_runner(turns)
    seed_calls: list = []
    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="plugin-real", runner=runner,
                                runtime=_NATIVE, workdir=tmp)
        orig_seed = agent._seed_plugin_store

        def _spy(task, store_dir, plugin_env):
            seed_calls.append(task.task_id)
            return orig_seed(task, store_dir, plugin_env)

        agent._seed_plugin_store = _spy  # type: ignore[assignment]

        s = [Session(session_id="s0", content="prior", timestamp=0.0)]
        t = _qa_task("solo", group_id=None, sessions=s)
        assert agent._plugin_group_store(t) is None   # no group store for ungrouped
        agent.solve(t, _Ctx())

        # No _groupstore tree exists under the run root.
        groupstore_root = Path(tmp) / "plugin-real" / "_groupstore"
        assert not groupstore_root.exists()
        # Per-task store still got its own (per-task) seed — behavior unchanged.
        assert seed_calls == ["solo"]
        assert (agent._task_dir(t) / ".cookbook-memory").is_dir()


# --------------------------------------------------------------------------- #
# Drain is a no-op under the fake runner (no real daydream-cli / transcript).
# --------------------------------------------------------------------------- #
def test_drain_is_noop_under_fake_runner() -> None:
    turns: dict = {}
    runner = _make_fake_plugin_runner(turns)
    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="plugin-real", runner=runner,
                                runtime=_NATIVE, workdir=tmp)
        store = Path(tmp) / "store"
        store.mkdir()
        # before_writes high so even a real poll would not "see" a new write; the
        # fake-runner guard must short-circuit before any polling/sleeping.
        import time as _time
        t0 = _time.monotonic()
        agent._drain_daydream(_qa_task("x", group_id="g"),
                              ClaudeResult(text="", raw={}), store,
                              store / "events.jsonl", {}, before_writes=999)
        assert _time.monotonic() - t0 < 1.0   # returned immediately (no poll loop)


def test_discover_transcript_resolves_sandbox() -> None:
    """Regression: _discover_transcript calls sandbox.active_config_dir(), but the
    `sandbox` module is imported only inside _ensure_real_plugin. Without a local
    import in this method it raised NameError in the daydream-drain backstop —
    failing the task AFTER a successful solve (seen on a real plugin-real run).
    It must resolve `sandbox` and return cleanly."""
    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="plugin-real",
                                runner=_make_fake_plugin_runner({}),
                                runtime=_NATIVE, workdir=tmp)
        store = Path(tmp) / "store"
        store.mkdir()
        # A truthy session_id makes the method proceed PAST the session_id guard to
        # `sandbox.active_config_dir()` (the NameError site). With the fix it returns
        # cleanly; without the local import it raised NameError here.
        result = agent._discover_transcript(
            ClaudeResult(text="", raw={"session_id": "sess-xyz"}), store)
        assert isinstance(result, tuple) and len(result) == 2


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
