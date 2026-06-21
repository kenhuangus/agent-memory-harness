# v0.1 benchmark results — Claude Code built-in memory vs our plugin memory

**Model:** `claude-haiku-4-5` · **Auth:** Claude Code subscription only (no API key) ·
**Date:** 2026-06-21 · **Memory version:** v0.1 (plugin = OKF-backed MCP "walking skeleton").

Each benchmark was run in two memory modes through the Claude Code CLI:

- **builtin** — Claude Code's *own* memory: prior sessions written as files, retrieved with its native Grep/Read.
- **plugin** — *our* memory: an MCP server (`memory_recall`/`memory_remember`) over an OKF store.

The raw per-benchmark records are the JSON files in this directory. This page is the honest read of them.

## Headline (the two discriminative QA benchmarks)

| Benchmark | builtin acc | plugin acc (raw) | plugin memory-reach | plugin acc, memory-reached only |
|---|---:|---:|---:|---:|
| MemoryAgentBench | **0.75** | 0.40 | 13/20 (65%) | n/a¹ |
| LongMemEval | **0.35** | 0.20 | 15/20 (75%) | **0.267** (4/15) |

¹ The per-task trajectory needed to compute MemoryAgentBench's conditional accuracy was
overwritten by later re-run attempts; only the aggregate survived. A clean re-run is pending
(see "Known issues" → WSL).

**Honest conclusion:** as of v0.1, **Claude Code's built-in memory currently outperforms our
plugin on both QA benchmarks.** This holds even after correcting for the plugin's connection
problem: on LongMemEval, accuracy computed over *only* the tasks where the plugin actually
reached memory is **0.267**, still below builtin's **0.35** (the 5 tasks that missed memory all
failed, 0/5). The plugin is an early walking skeleton — these numbers are a baseline to beat, not
a win.

## Code benchmarks (not yet a memory signal)

| Benchmark | builtin acc | plugin acc | note |
|---|---:|---:|---|
| ContextBench | 0.00 | 0.00 | 3 tasks timed out at 600 s |
| SWE-Bench-CL | 0.00 | 0.00 | — |
| SWE-ContextBench | 0.00 | 0.00 | — |

These are SWE-Bench-Verified **code** tasks: scoring requires applying a **unified diff** patch
and running the repo's tests. The SWE-bench Docker grader is now wired in (PR #32), but the agent
currently emits **prose**, not a diff, so every code task grades to `False`/ungraded. **The 0.00s
here are a harness limitation, not a memory result.** Real code numbers need the agent to emit
diffs (tracked follow-up).

## What changed this cycle (engineering)

- **MCP startup-race fixed (PR #30).** Headless `claude -p` began generating before its async MCP
  connection registered tools, so the plugin reached memory only ~65–75% of the time (the raw
  plugin numbers above are dragged down by those blind-answer tasks). A priming turn over
  stream-json closes the race: **first-try recall 40% → 100%** (20/20 needle test; every completed
  re-run task reached memory). A clean QA re-run on the fixed code is pending a stable WSL VM.
- **Docker grader wired (PR #32).** `SWEBenchDockerGrader` auto-selected for CODE benches; proven
  gold-patch → resolved, empty-patch → not-resolved in a real container.
- **Runner hardened (PR #26).** Parallel CLI processes, incremental per-task result saves, and a
  `reliability` block in every record documenting failures + `memory_reached`.
- **`memeval-bench` CLI (PR #33).** Run any benchmark on its own: `memeval-bench --benchmark longmemeval --mode plugin`.

## Reading the metrics

`accuracy` is the headline. `recency`/`relevancy`/`efficiency` are **memory-quality** metrics that
only apply to the plugin path (builtin uses native file memory, so they read 0). Some `efficiency`
values are anomalously large (e.g. 301) — a known metric-calculation quirk, separate from accuracy;
don't read them as real ratios yet.

## Reproduce

```bash
cd eval && pip install -e ".[claudecode,hf]"
memeval-bench --benchmark longmemeval --mode all --model claude-haiku-4-5 \
    --out-dir runs/claudecode --results runs/claudecode/results.json
python -m memeval.results summary --path runs/claudecode/results.json
```

Known issues / pending: a clean post-fix QA re-run (the host's WSL VM was crashing during this
session); diff-emitting agent for real code scores.
