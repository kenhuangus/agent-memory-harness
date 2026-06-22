# v0.1 benchmark results — Claude Code built-in memory vs our plugin memory

**Model:** `claude-haiku-4-5` · **Auth:** Claude Code subscription only (no API key) ·
**Date:** 2026-06-21 · **Memory version:** v0.1 (plugin = OKF-backed MCP "walking skeleton").

Each benchmark was run in two memory modes through the Claude Code CLI:

- **builtin** — Claude Code's *own* memory: prior sessions written as files, retrieved with its native Grep/Read.
- **plugin** — *our* memory: an MCP server (`memory_recall`/`memory_remember`) over an OKF store.

The raw per-benchmark records are the JSON files in this directory. This page is the honest read of them.

## Correction (2026-06-21): the LongMemEval plugin gap is NOT recall

An earlier diagnosis claimed the plugin "retrieved gold 0/15" and lost on **recall**. That was a
**measurement artifact**: `RetrievedItem.is_gold` was annotated only on in-memory trajectories and
never persisted, so the logged JSONL read all-`False` (fixed in [#46](https://github.com/kenhuangus/agent-memory-harness/pull/46)). The recorded `recency = 0.75`
already proved gold *was* retrieved. Re-checking the LongMemEval plugin run against the dataset's
gold ids and the full haystack:

- **Gold was actually retrieved in ~12/15 reached tasks** (raw id-match) — recall was never the bottleneck.
- Failure breakdown of the 20-task plugin run: 1 recall miss · 3 answer-not-in-content · **9 gold retrieved + answer present but the model still answered wrong** · 0 grading errors · ~4 correct.
- So the dominant gap is **long-context answer extraction**: retrieved items are *whole sessions* (3k–9k chars), and the answer, though present, is buried in noise. Builtin wins because `grep` hands the model small, targeted matched lines.

**Implications for the BM25 change ([#43](https://github.com/kenhuangus/agent-memory-harness/pull/43)):** it is still a genuine improvement — it re-ranks gold off the
~0.007 Jaccard tie-floor to the top (offline replay: gold recall@5 12/15 → 15/15) and repairs the
relevancy metric — but it helps **ranking**, not recall, and won't by itself close the extraction
gap. The next lever is **turn-level chunking** (small, targeted memory items instead of whole
sessions) — see [`suggestion.md`](../../suggestion.md) for the full set of memory-team suggestions.

**Measured (live plugin re-run on the BM25 code, `results/v0.1-bm25/`, n=20, reach 20/20):**

| metric | pre-BM25 (v0.1) | BM25 |
|---|---:|---:|
| accuracy | 0.20 | **0.25** |
| relevancy | 0.005 | **0.57** |
| recency | 0.75 | **0.84** |

This is exactly the predicted shape: BM25 dramatically improves **ranking** (relevancy and recency —
gold is now surfaced at the top) but only nudges **accuracy** (+0.05), still below builtin's 0.35,
because the binding constraint is long-context answer extraction, not retrieval. Confirms turn-level
chunking as the next lever (full memory-team suggestions in [`suggestion.md`](../../suggestion.md)).

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

These are SWE-Bench-Verified **code** tasks: scoring requires applying a patch and running the
repo's tests. CODE now runs as a real coding agent (`--code-mode agentic`) graded by host-local
test execution (`LocalExecGrader`), retired the prior approach where the agent emitted prose
instead of a diff. **The 0.00s here are from that earlier cycle, not a memory result.** Note the
local-exec grader is host-dependent and partial-coverage (it reports `None`/ungraded when a repo's
env can't be built), so it is not comparable to a containerized SWE-bench leaderboard — see
`docs/adrs/ADR-eval-002-docker-free-code-grading.md`.

## What changed this cycle (engineering)

- **MCP startup-race fixed (PR #30).** Headless `claude -p` began generating before its async MCP
  connection registered tools, so the plugin reached memory only ~65–75% of the time (the raw
  plugin numbers above are dragged down by those blind-answer tasks). A priming turn over
  stream-json closes the race: **first-try recall 40% → 100%** (20/20 needle test; every completed
  re-run task reached memory). A clean QA re-run on the fixed code is pending a stable WSL VM.
- **CODE grader (PR #32, since superseded).** A container-based grader was wired for CODE benches at
  the time. It has since been **removed** in favor of a Docker-free host-local `LocalExecGrader` plus
  an agentic coding loop (ADR-eval-002); CODE grading no longer needs Docker or any extra package.
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
