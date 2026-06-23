"""Offline tests for the plugin-real SHARED memory substrate (ADR-eval-003 / ADR-harness-012).

These prove, deterministically and with no network / no real ``claude`` / no real
``daydream-cli``, that:

* when a shared ``project_dir`` is configured, every plugin-real task points
  ``MEMORY_STORE`` at that ONE directory's ``.cookbook-memory`` store, so memory
  accumulates across tasks purely because the directory persists -- the harness
  copies nothing;
* without a shared ``project_dir``, each task gets its own per-task store (no
  cross-task carryover) -- the no-substrate path;
* the harness performs NO store management on the plugin-real path: the deleted
  group-store copy machinery (``_copy_store_contents`` / ``_group_restore`` /
  ``_group_persist`` / ``_plugin_group_store`` / ``_seed_plugin_store``) is gone, so
  the boundary "only the plugin touches the store" is structural, not disciplinary;
* the daydream drain is a pure wait-barrier and no-ops under a fake runner.

The fake runner stands in for the installed plugin: it writes a marker "memory" file
(and a recall event) into ``$MEMORY_STORE``, exactly where a real daydream write would
land. Run under the swebench venv with PYTHONPATH=. from eval/.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Make the package importable when run directly.
_THIS = Path(__file__).resolve()
_BASE_DIR = _THIS.parent.parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from memeval.claudecode import agent as A  # noqa: E402
from memeval.claudecode.agent import ClaudeCodeAgent  # noqa: E402
from memeval.claudecode.cli import ClaudeResult  # noqa: E402
from memeval.claudecode.platform import ClaudeRuntime  # noqa: E402
from memeval.schema import Benchmark, Task, TaskKind  # noqa: E402

SkipTest = unittest.SkipTest
_NATIVE = ClaudeRuntime(kind="native", exe="claude", python="python")


class _Ctx:
    """Minimal AgentContext stand-in."""

    def record_generate(self, *a, **k) -> None: ...
    def record_retrieve(self, *a, **k) -> None: ...
    def note(self, *a, **k) -> None: ...


class _RecordingCtx(_Ctx):
    def __init__(self) -> None:
        self.retrieves: list[tuple[list, str]] = []

    def record_retrieve(self, hits, *, query: str = "") -> None:
        self.retrieves.append((list(hits), query))


def _qa_task(task_id: str, *, group_id=None, order=0) -> Task:
    return Task(
        task_id=task_id, benchmark=Benchmark.SWE_BENCH_CL, kind=TaskKind.QA,
        question=f"q-{task_id}", group_id=group_id, order=order,
    )


def _read_events(store: Path) -> list[dict]:
    ev = store / "events.jsonl"
    if not ev.exists():
        return []
    return [json.loads(line) for line in ev.read_text().splitlines() if line.strip()]


def _make_fake_plugin_runner(turns: dict):
    """Stand in for the installed plugin. Each turn records which marker files the
    store the plugin resolves (``$MEMORY_STORE``) ALREADY holds
    when the turn starts, then writes this task's marker + a recall event there."""

    def fake(prompt, *, cwd, extra_env=None, **kw) -> ClaudeResult:
        env = extra_env or {}
        store = Path(
            env.get("MEMORY_STORE")
            or (Path(env.get("CLAUDE_PROJECT_DIR", cwd)) / ".cookbook-memory")
        )
        store.mkdir(parents=True, exist_ok=True)
        turns.setdefault("seen", []).append(sorted(p.name for p in store.glob("mem_*.md")))
        turns.setdefault("stores", []).append(str(store))
        marker = (prompt.split("q-")[-1].split()[0] if "q-" in prompt else "x")
        (store / f"mem_{marker}.md").write_text(f"learned in {marker}\n", encoding="utf-8")
        ev = {"ts": 1.0, "op": "recall", "ids": ["m1"], "query": prompt,
              "meta": {"hits": [{"id": "m1", "content": "x", "score": 0.9, "rank": 0,
                                 "tokens": 1, "timestamp": 1.0}]}}
        with open(store / "events.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev) + "\n")
        return ClaudeResult(text="done", tokens_in=5, tokens_out=1)

    return fake


# --------------------------------------------------------------------------- #
# Shared substrate: every task points at ONE store; memory accumulates by
# persistence with NO harness copy.
# --------------------------------------------------------------------------- #
def test_shared_project_dir_accumulates_memory_across_tasks() -> None:
    turns: dict = {}
    runner = _make_fake_plugin_runner(turns)
    with tempfile.TemporaryDirectory() as tmp:
        substrate = Path(tmp) / "_memory"
        agent = ClaudeCodeAgent(memory_mode="plugin-real", runner=runner,
                                runtime=_NATIVE, workdir=tmp, project_dir=substrate)
        t1 = _qa_task("cl_1", group_id="seqA", order=0)
        t2 = _qa_task("cl_2", group_id="seqA", order=1)
        agent.solve(t1, _Ctx())
        agent.solve(t2, _Ctx())

        store = (substrate / ".cookbook-memory").resolve()  # agent resolves the project dir
        # Both tasks resolved the SAME shared store.
        assert turns["stores"][0] == turns["stores"][1] == str(store)
        # Task 1 saw an empty store; task 2 saw task 1's memory -- because the dir
        # persisted, NOT because the harness copied anything.
        assert turns["seen"][0] == []
        assert "mem_cl_1.md" in turns["seen"][1]
        # The shared store holds BOTH tasks' memory.
        assert sorted(p.name for p in store.glob("mem_*.md")) == ["mem_cl_1.md", "mem_cl_2.md"]
        # The harness never wrote into the shared store itself -- only the (fake) plugin did.
        # Sanity: no per-task .cookbook-memory under the run tree holds the markers.
        assert turns["seen"][0] == [] and "mem_cl_1.md" in turns["seen"][1]


def test_no_project_dir_keeps_per_task_stores_isolated() -> None:
    turns: dict = {}
    runner = _make_fake_plugin_runner(turns)
    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="plugin-real", runner=runner,
                                runtime=_NATIVE, workdir=tmp)  # no project_dir
        t1 = _qa_task("cl_1", group_id="seqA", order=0)
        t2 = _qa_task("cl_2", group_id="seqA", order=1)
        agent.solve(t1, _Ctx())
        agent.solve(t2, _Ctx())

        # Distinct per-task stores; task 2 did NOT inherit task 1's memory.
        assert turns["stores"][0] != turns["stores"][1]
        assert turns["seen"][1] == []  # nothing carried over without a shared substrate
        store1 = agent._task_dir(t1) / ".cookbook-memory"
        store2 = agent._task_dir(t2) / ".cookbook-memory"
        assert len(_read_events(store1)) == 1 and len(_read_events(store2)) == 1


# --------------------------------------------------------------------------- #
# Boundary: the harness performs no store management -- the copy machinery is gone.
# --------------------------------------------------------------------------- #
def test_harness_has_no_store_management_machinery() -> None:
    # The deleted group-store copy helpers must not exist -- the boundary is structural.
    for name in (
        "_copy_store_contents", "_group_store_has_memory", "_GROUP_STORE_EXCLUDE",
        "_GROUP_SEEDED_SENTINEL", "_wait_for_daydream",
    ):
        assert not hasattr(A, name), f"{name} should be deleted (ADR-harness-012)"
    for meth in (
        "_group_restore", "_group_persist", "_plugin_group_store",
        "_seed_plugin_store", "_drive_sessions_through_daydream",
    ):
        assert not hasattr(ClaudeCodeAgent, meth), f"{meth} should be deleted (ADR-harness-012)"

    # The plugin-real source path must not copy/exclude the store.
    import inspect
    src = inspect.getsource(ClaudeCodeAgent._solve_plugin_real)
    src += inspect.getsource(ClaudeCodeAgent._run_code_agent_turn)
    for forbidden in ("copytree(", "_copy_store_contents", "_group_persist", "_group_restore"):
        assert forbidden not in src, f"plugin-real path must not use {forbidden} (ADR-eval-003)"


def test_agentic_code_keeps_checkout_cwd_but_shared_store() -> None:
    # In agentic CODE the cwd is the per-task checkout, but the memory store is the
    # shared substrate (MEMORY_STORE), decoupling edits from memory location.
    seen: dict = {}

    def fake(prompt, *, cwd, extra_env=None, **kw) -> ClaudeResult:
        seen.setdefault("cwd", []).append(str(cwd))
        seen.setdefault("project_dir", []).append((extra_env or {}).get("CLAUDE_PROJECT_DIR"))
        seen.setdefault("memory_store", []).append((extra_env or {}).get("MEMORY_STORE"))
        env = extra_env or {}
        store = Path(
            env.get("MEMORY_STORE")
            or (Path(env.get("CLAUDE_PROJECT_DIR", cwd)) / ".cookbook-memory")
        )
        store.mkdir(parents=True, exist_ok=True)
        (store / "events.jsonl").write_text(
            json.dumps({"ts": 1.0, "op": "recall", "ids": [], "query": prompt,
                        "meta": {"hits": []}}) + "\n", encoding="utf-8")
        return ClaudeResult(text="done", tokens_in=1, tokens_out=1)

    import memeval.claudecode.agent as agmod
    with tempfile.TemporaryDirectory() as tmp:
        substrate = Path(tmp) / "_memory"
        agent = ClaudeCodeAgent(memory_mode="plugin-real", runner=fake, runtime=_NATIVE,
                                workdir=tmp, code_mode="agentic", project_dir=substrate)
        task = Task(task_id="code_1", benchmark=Benchmark.SWE_BENCH_CL, kind=TaskKind.CODE,
                    question="fix it", repo="o/r", base_commit="abc", group_id="seqA", order=0)
        # Stub checkout/diff so no real git runs; the store-location assertion is the point.
        orig_prepare, orig_capture = agmod.prepare_checkout, agmod.capture_diff
        agmod.prepare_checkout = lambda *a, **k: None
        agmod.capture_diff = lambda *a, **k: ""
        try:
            agent.solve(task, _Ctx())
        finally:
            agmod.prepare_checkout, agmod.capture_diff = orig_prepare, orig_capture

        # cwd was the per-task checkout; MEMORY_STORE was the shared store.
        resolved = str(substrate.resolve())
        assert any(resolved == pd for pd in seen["project_dir"])
        assert any(
            str(substrate.resolve() / ".cookbook-memory") == ms
            for ms in seen["memory_store"]
        )
        assert all("repo" in c for c in seen["cwd"])  # ran in the checkout
        assert (substrate.resolve() / ".cookbook-memory").is_dir()


def test_zero_hit_real_recall_is_attributed_as_reach() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / "store"
        store.mkdir()
        (store / "events.jsonl").write_text(
            json.dumps({"ts": 1.0, "op": "recall", "ids": [], "query": "empty",
                        "meta": {"hits": []}}) + "\n",
            encoding="utf-8",
        )
        agent = ClaudeCodeAgent(memory_mode="plugin-real", runner=lambda *a, **k: ClaudeResult(text=""),
                                runtime=_NATIVE, workdir=tmp)
        ctx = _RecordingCtx()

        agent._attribute_real_recall(store / "events.jsonl", ctx)

        assert len(ctx.retrieves) == 1
        assert ctx.retrieves[0][0] == []
        assert ctx.retrieves[0][1] == "empty"


# --------------------------------------------------------------------------- #
# Drain is a pure wait-barrier; no-ops under the fake runner.
# --------------------------------------------------------------------------- #
def test_drain_is_noop_under_fake_runner() -> None:
    turns: dict = {}
    runner = _make_fake_plugin_runner(turns)
    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="plugin-real", runner=runner,
                                runtime=_NATIVE, workdir=tmp)
        store = Path(tmp) / "store"
        store.mkdir()
        import time as _time
        t0 = _time.monotonic()
        agent._drain_daydream(_qa_task("x", group_id="g"),
                              ClaudeResult(text="", raw={}), store,
                              store / "events.jsonl", {}, before_writes=999)
        assert _time.monotonic() - t0 < 1.0   # returned immediately (no poll loop)


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
