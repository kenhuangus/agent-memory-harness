"""Probe real dataset group/sequence sizes to set per-benchmark long-memory floors.

Loads each benchmark's REAL source via its memeval loader (no API key needed),
then reports, per benchmark: number of tasks, number of groups (group_id), and
the distribution of group sizes + sessions-per-task. This tells us how big a
--limit must be for the continual-learning code benches to exercise memory
across whole sequences, and confirms the QA benches are multi-session per task.
"""
from __future__ import annotations

import statistics as st
from collections import Counter

from memeval.loaders import get_loader
from memeval.schema import Benchmark

BENCHES = ["longmemeval", "memoryagentbench", "swe_bench_cl", "swe_contextbench", "contextbench"]


def summarize(name: str) -> None:
    try:
        loader = get_loader(Benchmark.from_str(name))
        tasks = loader.load(None, limit=None)  # real source, full
    except Exception as exc:
        print(f"\n## {name}\n  LOAD FAILED: {type(exc).__name__}: {str(exc)[:200]}")
        return

    n = len(tasks)
    groups = Counter(t.group_id or "(none)" for t in tasks)
    gsizes = sorted(groups.values())
    sess = [len(t.sessions) for t in tasks]

    print(f"\n## {name}")
    print(f"  tasks={n}  groups={len(groups)}")
    if gsizes:
        print(f"  group size: min={gsizes[0]} median={int(st.median(gsizes))} "
              f"max={gsizes[-1]} mean={st.mean(gsizes):.1f}")
    if sess:
        print(f"  sessions/task: min={min(sess)} median={int(st.median(sess))} "
              f"max={max(sess)} mean={st.mean(sess):.1f}")
    # How many entries to cover K whole groups (smallest groups first vs largest)?
    if len(groups) > 1:
        cum = 0
        for k, gs in enumerate(gsizes, 1):
            cum += gs
            if k in (1, 3, 5, 10):
                print(f"  entries to cover {k} smallest whole group(s): {cum}")


if __name__ == "__main__":
    for b in BENCHES:
        summarize(b)
