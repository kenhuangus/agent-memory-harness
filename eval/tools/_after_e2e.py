"""AFTER measurement through the REAL production code path.

Drives ClaudeCodeAgent(memory_mode="plugin", transport="http") -> _run_plugin_http
-> run_claude_primed for N trials with a unique needle. Reports first-try recall
(recall.jsonl grew on the FIRST primed attempt) and needle-in-answer.
"""
from __future__ import annotations
import os, sys, time, random, tempfile, shutil
from pathlib import Path

WT = "/mnt/c/Users/kenhu/amh-mcp-fix"
sys.path.insert(0, f"{WT}/eval")

import memeval, inspect
src = inspect.getfile(memeval)
assert src.startswith(WT), f"memeval not from worktree: {src}"
print(f"memeval from: {src}")

from memeval.claudecode.agent import ClaudeCodeAgent, _count_recalls
from memeval.claudecode.platform import detect
from memeval.schema import Task, Session, Benchmark, TaskKind

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
rt = detect()
assert rt is not None, "no claude runtime detected"
print(f"runtime: kind={rt.kind} exe={rt.exe}")


class Ctx:
    """Minimal AgentContext: records retrieve/generate calls."""
    def __init__(self):
        self.retrieves = []
        self.generates = []
    def record_retrieve(self, hits, query=""):
        self.retrieves.append((query, hits))
    def record_generate(self, text, tin, tout, model_name=None):
        self.generates.append(text)


first_ok = 0
needle_ok = 0
root = Path(tempfile.mkdtemp(prefix="amh-after-"))
try:
    for t in range(1, N + 1):
        needle = f"ZEPHYR-{int(time.time())}-{random.randint(1000,9999)}-{t}"
        task = Task(
            task_id=f"needle-{t}",
            benchmark=Benchmark.LONGMEMEVAL,
            kind=TaskKind.QA,
            question="What is the secret project code?",
            sessions=[Session(session_id="s1",
                              content=f"Note: the secret project code is {needle}.",
                              timestamp=0.0)],
            answer=needle,
        )
        agent = ClaudeCodeAgent(memory_mode="plugin", transport="http",
                                runtime=rt, workdir=root / f"t{t}", timeout=300)
        # Patch _run_plugin_http loop start so we can detect FIRST-attempt success:
        # we count recalls before the agent runs and after; with the priming fix the
        # very first primed attempt should log a recall. To isolate first-try, we cap
        # tries to 1 for this measurement by monkeypatching the module constant.
        import memeval.claudecode.agent as A
        A._PLUGIN_MAX_TRIES = 1  # measure FIRST-try only — no backstop retries
        ctx = Ctx()
        ans = agent.solve(task, ctx)
        log = root / f"t{t}" / "plugin" / f"needle-{t}" / "recall.jsonl"
        recalled = _count_recalls(log) > 0
        has_needle = needle in (ans or "")
        if recalled:
            first_ok += 1
        if recalled and has_needle:
            needle_ok += 1
        print(f"  {t:2d}: {'CALLED ' if recalled else 'NOT    '} "
              f"needle={'yes' if has_needle else 'no '} | {(ans or '')[:70]!r}")
    print(f"AFTER (real path, max_tries=1) first_try_recall={first_ok}/{N}  "
          f"recall+needle={needle_ok}/{N}")
finally:
    shutil.rmtree(root, ignore_errors=True)
