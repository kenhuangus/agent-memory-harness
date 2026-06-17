# memeval — AI Agent Memory Harness (codename: Cookbook Memory) evaluation infra

Evaluation infrastructure for the **AI Agent Memory Harness**.

> **Hypothesis under test:** a small model (**Claude Haiku**) *plus* the memory
> harness can beat **Claude Opus 4.8 with no memory** on public memory
> benchmarks. `memeval` is the apparatus that measures whether that's true —
> the same harness drives five public benchmarks through one unified data model
> and scores every run on the same four metrics, memory-ON vs memory-OFF.

- **Distribution name:** `agent-memory-eval`
- **Import package:** `memeval` (deliberately *not* `eval`, to avoid shadowing
  the builtin)
- **Python:** 3.11+ (developed/targeted on 3.13)
- **Run + read it:** see [`PROTOCOL.md`](PROTOCOL.md) — the reproducible guide
  for running benchmarks, real CODE grading, and reading the hypothesis scoreboard.

---

## What it is

`memeval` normalizes four very different benchmarks into one `Task` shape so a
single `run(benchmark, model, memory)` can drive them all, logs a reproducible
`Trajectory` per task, and scores each run on four metrics. The contract
(`schema.py` + `protocols.py`) is **frozen** — every module builds against it,
so loaders, stores, and model adapters are written independently and still
compose.

```
memeval/
  schema.py        # FROZEN data model: Task, Session, MemoryItem, Trajectory, Metrics, RunResult ...
  protocols.py     # FROZEN seams: MemoryStore, ModelAdapter, Loader (typing.Protocol)
  metrics.py       # recency, efficiency, relevancy, accuracy + compute_metrics  (stdlib)
  trajectory.py    # TrajectoryLogger + JSONL reader (consumed by the dreaming worker)  (stdlib)
  cost.py          # CostTracker, BudgetExceeded, PRICING, load_key_config, cheapest_first  (stdlib)
  models.py        # EchoModel (offline) + AnthropicAdapter (lazy-imports anthropic)
  harness.py       # run(...) -> RunResult ; InMemoryStore ; cheapest-first ordering; early-exit
  cli.py           # python -m memeval.cli run ...
  loaders/         # registry + one loader per benchmark (local=stdlib, remote=lazy datasets)
  config/keys.example.json   # per-captain / per-benchmark sharded-eval config
tests/
  test_smoke.py    # OFFLINE smoke tests (stdlib only; run with or without pytest)
  fixtures/        # tiny hand-written sample per benchmark
```

### The offline guarantee (load-bearing)

The **offline path** — parsing local fixtures, all metrics math, the harness
with `EchoModel` + `InMemoryStore`, cost gating, trajectory JSONL IO, and the
smoke tests — runs on the **Python standard library alone**. There are **no
required runtime dependencies**. Every heavy dependency (`anthropic`,
`datasets`, `numpy`, `pyyaml`, `requests`, `faiss`) is optional and imported
*lazily inside the function that needs it*, never at module top level. So you
can clone, `pip install -e .`, and immediately run the smoke tests and the
echo-model CLI with nothing else installed.

---

## Install

```bash
# from the eval/ directory (this README's directory)
pip install -e .                 # offline path only — stdlib, zero extra deps
```

Optional extras, added only when you need a live/remote capability:

| Extra            | Pulls in                                   | Needed for                                            |
|------------------|--------------------------------------------|-------------------------------------------------------|
| `anthropic`      | `anthropic`                                | `AnthropicAdapter` (real Claude calls)                |
| `hf`             | `datasets`                                 | loaders' remote download path (HuggingFace)           |
| `embeddings`     | `numpy`                                     | embedding-based relevancy / vector retrieval          |
| `full`           | anthropic + datasets + numpy + requests + pyyaml | real, online, paid runs                         |
| `dev`            | `pytest`                                   | `python -m pytest` (tests also run without it)        |

```bash
pip install -e ".[full]"         # everything for real online runs
pip install -e ".[anthropic]"    # just real Claude models, local fixtures
```

---

## Run the offline smoke tests

The smoke suite is **stdlib-only** and runs **two ways** — pytest is *not*
required:

```bash
# 1) as a pytest suite (if pytest is installed)
python -m pytest tests

# 2) as a plain script — no pytest needed; prints PASS/FAIL, exits nonzero on failure
python tests/test_smoke.py
```

It covers: fixture parsing for each of the five benchmarks, the metrics math
(recency / efficiency / relevancy / accuracy), the harness end-to-end with
`EchoModel` + `InMemoryStore`, the cost gate raising `BudgetExceeded`, and the
trajectory JSONL round-trip.

## Run the harness from the CLI (offline)

The `echo` model is deterministic and free, and the in-memory store is
stdlib-only, so the whole loop runs offline against a fixture:

```bash
# offline: echo model, no memory, against the bundled LongMemEval fixture
python -m memeval.cli run \
    --benchmark longmemeval \
    --model echo \
    --no-memory \
    --path tests/fixtures/longmemeval.json \
    --limit 5

# memory-ON, same fixture, write trajectories to JSONL for the dreaming worker
python -m memeval.cli run \
    --benchmark longmemeval \
    --model echo \
    --memory \
    --path tests/fixtures/longmemeval.json \
    --out runs/longmemeval_echo_mem.jsonl
```

The `run` subcommand prints `RunResult.to_dict()` as JSON (benchmark, config,
the four metrics, token/cost totals, partial/budget flags). Key flags:

| Flag                         | Meaning                                                        |
|------------------------------|---------------------------------------------------------------|
| `--benchmark`                | `longmemeval` \| `memoryagentbench` \| `swe_contextbench` \| `swe_bench_cl` (loose names accepted via `Benchmark.from_str`) |
| `--model`                    | adapter id; default `echo` (offline). Real ids: `claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-opus-4-8` |
| `--memory` / `--no-memory`   | toggle the memory path (memory-ON vs memory-OFF cell)         |
| `--limit N`                  | cap tasks (cheap dev iteration)                               |
| `--dev-slice F`              | stratified dev slice (fraction in (0,1] or an int count), seeded & deterministic |
| `--path`                     | local benchmark/fixture JSON (offline, stdlib). Omit to use the loader's remote `default_source` (lazy `datasets`) |
| `--budget-usd` / `--budget-tokens` | hard caps; abort → `partial` RunResult on `BudgetExceeded` |
| `--keys` + `--captain`       | sharded-key config (see below)                               |
| `--out`                      | write per-task trajectories as JSONL                          |

A live run swaps `--model echo` for a real Claude id and provides a key — see
the cost workflow below.

---

## The five benchmarks → loaders → metrics

Each loader normalizes its source into `list[Task]`. The local-file path is
stdlib-only; the remote path lazily imports `datasets`. Resolve a loader with
`memeval.loaders.get_loader(benchmark)`.

| Benchmark | Loader | `default_source` | Kind | Real source |
|---|---|---|---|---|
| **MemoryAgentBench** | `MemoryAgentBenchLoader` | `ai-hyz/MemoryAgentBench` | QA | HF `ai-hyz/MemoryAgentBench`; GitHub `HUST-AI-HYZ/MemoryAgentBench`; arXiv 2507.05257. Competencies: accurate retrieval, test-time learning, long-range understanding, conflict resolution (EventQA, FactConsolidation). |
| **LongMemEval** | `LongMemEvalLoader` | `xiaowu0162/LongMemEval` | QA | GitHub `xiaowu0162/LongMemEval`; arXiv 2410.10813. Files `longmemeval_s.json` (~115k tok/q), `longmemeval_m.json` (~1.5M), `longmemeval_oracle.json`. Multiple timestamped sessions/question; abilities incl. temporal reasoning, knowledge updates, abstention. |
| **SWE-ContextBench** | `SWEContextBenchLoader` | `jiayuanz3/SWEContextBench` | CODE | HF `jiayuanz3/SWEContextBench`; GitHub `jiayuanz3/SWEContextBench`; arXiv 2602.08316. Parquet files (Experience + Related + Relationship; `lite=True` for Lite_* subsets), SWE-bench column schema. 1,100 base + 376 related, 51 repos, 9 languages; `group_id` from the Relationship links. |
| **SWE-Bench-CL** | `SWEBenchCLLoader` | `thomasjoshi/agents-never-forget` | CODE | GitHub `thomasjoshi/agents-never-forget`; arXiv 2507.00014. Built on SWE-bench Verified; chronological per-repo issue *sequences* (`group_id` = sequence, `order` within it). Continual-learning metrics. |
| **ContextBench** | `ContextBenchLoader` | `Contextbench/ContextBench` | CODE | HF `Contextbench/ContextBench` (configs `default` / `contextbench_verified`, single `train` split; `verified=True` for the 500-task subset); GitHub `EuniAI/ContextBench`; arXiv 2602.05892. In-task retrieval quality: 1,136 tasks, 66 repos, 8 langs, human-annotated `gold_context` spans (file/block/line) → `sessions` + `gold_memory_ids`. Primary metrics: relevancy + efficiency. |

All defaults are overridable via the `--path` flag, CLI args, or env.

### How a run produces the four metrics

`harness.run(...)` loads tasks, optionally consults the `MemoryStore` when
`memory=True`, records a `Trajectory` per task, then `metrics.compute_metrics`
aggregates the four numbers from those trajectories + the gold task metadata
(`Metrics.to_dict()`):

- **recency** — of queries whose freshest gold-relevant memory was retrieved,
  the fraction where that freshest relevant item is ranked **#1** (`rank == 0`);
  also a decayed score `mean(exp(-dt / tau))` over each query's freshest
  relevant item (`dt = query_time − item_time`, `tau` default 1 day). *Higher is
  better.* Depends on the store setting `RetrievedItem.rank` and honoring
  `as_of` so it never peeks at the future.
- **efficiency** — `memory_tokens / total_tokens` overhead per retrieval,
  averaged over tasks (target `< ~0.10`). *Lower is better.* Depends on
  `search()` setting `RetrievedItem.item.tokens`.
- **relevancy** — mean cosine similarity (or the provided `score`) of retrieved
  items vs. the query, plus precision@k = fraction of retrieved items scoring
  `>= threshold` (default `0.7`). *Higher is better.*
- **accuracy** — task success rate (QA = normalized exact match / judge; CODE =
  patch resolves / tests pass), tracked **memory-ON vs memory-OFF** so the
  dashboard shows the lift (`Metrics.accuracy_lift`). *Higher is better.*

Cross-module invariant: **tokens flow through `RetrievedItem`** and **all
prices are USD per *million* tokens** everywhere (`ModelConfig.price_*`,
`ModelAdapter.price_*`, `cost.PRICING`).

---

## Trajectories (for the dreaming worker)

Every run can stream one `Trajectory` per task as JSONL via
`TrajectoryLogger` (or the CLI `--out`). The dreaming/consolidation worker
reads them back with `memeval.trajectory.read_trajectories(path)`, which
rebuilds the nested dataclasses and enums. One self-contained JSONL record per
trajectory — no need for the original benchmark to replay it.

```python
from memeval.trajectory import TrajectoryLogger, read_trajectories

with TrajectoryLogger("runs/lme.jsonl") as log:
    log.log(traj)                       # one JSONL line per trajectory, flushed

for traj in read_trajectories("runs/lme.jsonl"):
    ...                                 # consumed by the dreaming worker
```

---

## Multi-step agents (OpenCode integration)

`run` is a **single-shot** loop (one retrieve → one generate) — right for the
QA-style memory benchmarks. Coding agents run a **multi-step loop**, so
`memeval.agent` adds a sibling path **without touching the frozen contract**:

- **`AgentAdapter`** — the seam an agent implements: one method, `solve(task, ctx)`.
  **OpenCode plugs in here** (architecture A): Keith wraps the OpenCode loop as an
  `AgentAdapter` and, each step, calls `ctx.retrieve` / `ctx.remember` against the
  **shared `MemoryStore`** (his real memory harness, passed as `store=`) and reports
  generations via `ctx.generate` (or `ctx.record_generate` if OpenCode called its
  own model). Every op lands as a `TrajectoryStep`, so the existing metrics and the
  dreaming worker consume agent runs unchanged.
- **`AgentContext`** — keeps cost + trajectory + grading centralized; the agent only
  decides *what* to do. Memory methods are no-ops when memory is off, so the same
  agent code runs the memory-off baseline.
- **`run_agent(...)`** — the sibling of `run(...)`; same `RunResult`/metrics.
- **`EchoAgent`** — offline reference agent (a real retrieve→generate→write-back loop).

```python
from memeval.agent import run_agent, EchoAgent, AgentResult
from memeval.schema import Benchmark

# Offline demo (3-step loop, write-back accumulates memory across a group):
rr = run_agent(Benchmark.LONGMEMEVAL, EchoAgent(), memory=True,
               path_or_id="tests/fixtures/longmemeval.json")

# Real integration: pass Keith's memory harness as the shared store.
# rr = run_agent(Benchmark.SWE_BENCH_CL, OpenCodeAgent(), memory=True,
#                store=keiths_memory_harness, cost=tracker, grader=swe_grader)
```

OpenCode's `AgentAdapter` returns an `AgentResult(prediction=..., patch=..., success=...)`;
`success` (if set, e.g. after it runs the tests) overrides grading, otherwise a
`grader(task, prediction)` (the CODE grader — separate piece) decides solve-rate.

---

## Sharded-key cost workflow

Real runs are sharded **one captain (and key + budget) per benchmark** so four
people can evaluate in parallel without blowing a shared budget. The mapping
lives in `memeval/config/keys.example.json`:

| Benchmark            | Captain | `api_key_env`               | `budget_usd` |
|----------------------|---------|-----------------------------|--------------|
| `swe_bench_cl`       | Keith   | `ANTHROPIC_API_KEY_KEITH`   | 50.0         |
| `longmemeval`        | Ken     | `ANTHROPIC_API_KEY_KEN`     | 50.0         |
| `swe_contextbench`   | Brent   | `ANTHROPIC_API_KEY_BRENT`   | 50.0         |
| `memoryagentbench`   | Scott B.   | `ANTHROPIC_API_KEY_SCOTT`   | 50.0         |

`api_key_env` names the **environment variable** that holds the key — the key
value lives in your environment, never in the file. Copy the example, fill in
real env-var names, and keep `keys.json` out of version control:

```bash
cp memeval/config/keys.example.json memeval/config/keys.json
export ANTHROPIC_API_KEY_KEN="sk-ant-..."          # the captain's key, in the env only

# Ken runs his LongMemEval shard against real Claude, budget-capped:
python -m memeval.cli run \
    --benchmark longmemeval \
    --model claude-haiku-4-5 \
    --memory \
    --keys memeval/config/keys.json \
    --captain longmemeval \
    --out runs/longmemeval_haiku_mem.jsonl
```

`load_key_config(path)` reads that file into
`{benchmark: {api_key_env, budget_usd, budget_tokens?, captain?}}`; the harness
builds a `CostTracker(budget_usd=..., budget_tokens=...)` and aborts to a
**partial** `RunResult` the instant a shard would overspend — your spend and
token totals reflect the call that tripped the cap.

### Cheapest-first, so you spend on the cheap cells first

`cost.cheapest_first(configs)` (also re-exported from the harness) orders
configurations **Haiku+mem → Haiku → Sonnet → Opus** — memory-ON before
memory-OFF, then tier `haiku < sonnet < opus`, then blended price. Combined
with `harness.should_early_exit(...)`, you run the cheap+memory cell first and
stop climbing the price ladder once a configuration already clears the target
accuracy — directly testing the project hypothesis at minimum cost.

> **Prices are PLACEHOLDERS.** `cost.PRICING` ships plausible stand-ins (USD per
> **million** tokens) so the offline cost gate is exercisable. They are clearly
> marked in source and **must be verified against the current Anthropic price
> sheet before any paid run.**

---

## Programmatic use

```python
from memeval.schema import Benchmark
from memeval.harness import run, InMemoryStore
from memeval.models import EchoModel
from memeval.cost import CostTracker

result = run(
    Benchmark.LONGMEMEVAL,
    model=EchoModel(),
    memory=True,
    path_or_id="tests/fixtures/longmemeval.json",   # offline, stdlib
    store=InMemoryStore(),
    cost=CostTracker(budget_usd=5.0),
    limit=5,
)
print(result.to_dict())                              # metrics + cost summary
```

---

## License

MIT.
