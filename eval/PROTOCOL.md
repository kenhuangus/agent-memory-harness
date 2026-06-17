# Evaluation protocol — Cookbook Memory

> The reproducible **how to run + how to read the result** guide for the memory
> harness. Owner: **Ken** (evaluation infrastructure). Pairs with the contracts
> in [`../prd.md`](../prd.md), [`../architecture.md`](../architecture.md), and
> [`../plan.md`](../plan.md).

The eval answers one question: **does a cheap model + the memory harness close
the gap to a frontier model running without memory?** Concretely — can
*Haiku 4.5 + memory* match or beat *Opus 4.8, no memory* on public benchmarks,
without spending its savings on context tokens.

---

## 1. Install

```bash
cd eval
pip install -e .                 # offline core — stdlib only, zero required deps
pip install -e ".[anthropic]"    # real Claude models (AnthropicAdapter)
pip install -e ".[hf]"           # remote dataset download (HuggingFace datasets)
pip install -e ".[embeddings]"   # numpy — embedding-based relevancy / vector store
pip install -e ".[swebench]"     # real CODE scoring (also needs a Docker daemon)
pip install -e ".[full]"         # everything for a real (paid, online) run
```

The **offline path imports no third-party package**. Everything heavy
(`anthropic`, `datasets`, `numpy`, `swebench`) is lazy-imported only on the path
that needs it.

## 2. The experiment grid

Per benchmark, four configurations (the scoreboard reads the first two):

| Role | Config | Purpose |
|---|---|---|
| **treatment** | Haiku 4.5 + memory **on** | the bet: cheap + memory |
| **baseline** | Opus 4.8, memory **off** | the bar to beat |
| lower bound | Haiku 4.5, memory **off** | what memory adds |
| reference | Sonnet 4.6, memory **off** | mid-tier context |

Models: `claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-opus-4-8`, or `echo`
(free, offline, deterministic — for pipeline tests).

## 3. Run one benchmark

```bash
# offline smoke (free): EchoModel on the bundled fixture
python -m memeval.results run --benchmark longmemeval --model echo --no-memory \
    --path tests/fixtures/longmemeval.json --results ../results.json

# real treatment run, memory on, $10 budget cap (default), logged to the ledger
python -m memeval.results run --benchmark longmemeval --model claude-haiku-4-5 \
    --memory --budget-usd 10 --results ../results.json --run-id "$(date +%F)-haiku-mem"
```

Key flags: `--memory` / `--no-memory`; `--path <file>` (local) or omit for the
real remote source; `--limit N`; `--dev-slice 0.1` (stratified fraction);
`--budget-usd 10` (`<=0` disables the cap); `--k 5` (retrieval depth);
`--out runs/<name>.jsonl` (per-task trajectory log).

Programmatic equivalent:

```python
from memeval.harness import run
from memeval.models import get_model
from memeval.results import append_result
from memeval.schema import Benchmark
rr = run(Benchmark.LONGMEMEVAL, get_model("claude-haiku-4-5"), memory=True,
         path_or_id=None)            # None -> the real remote source
append_result(rr, "../results.json", run_id="haiku-mem")
```

## 4. Datasets (validated against their real sources)

| Benchmark | `--benchmark` | Source (HuggingFace) |
|---|---|---|
| MemoryAgentBench | `memoryagentbench` | `ai-hyz/MemoryAgentBench` (4 competency splits) |
| LongMemEval | `longmemeval` | `xiaowu0162/longmemeval` (`longmemeval_s` default ~278 MB; `longmemeval_oracle` ~15 MB) |
| SWE-ContextBench | `swe_contextbench` | `jiayuanz3/SWEContextBench` (parquet under `data/`) |
| SWE-Bench-CL | `swe_bench_cl` | `thomasjoshi/swe-bench-cl` (`SWE-Bench-CL.json`) |
| ContextBench | `contextbench` | `Contextbench/ContextBench` |

Pass a smaller variant by name, e.g. `--path longmemeval_oracle`. Validate every
loader against its live source (network; tiny limits):

```bash
MEMEVAL_LIVE=1 python -m pytest tests/test_smoke.py -k live
```

## 5. CODE grading (SWE-ContextBench, SWE-Bench-CL, ContextBench)

QA tasks grade by normalized exact match automatically. CODE tasks need the
patch applied and tests run — choose a grader:

```bash
# real score: official SWE-bench harness in per-task Docker containers
python -m memeval.results run --benchmark swe_bench_cl --model claude-haiku-4-5 \
    --memory --grader swebench --results ../results.json

# offline heuristic (smoke only — token overlap vs the gold patch, NOT tests)
python -m memeval.results run --benchmark swe_bench_cl --model echo --no-memory \
    --path tests/fixtures/swe_bench_cl.json --grader overlap --results ../results.json
```

**Resolved rule (SWE-bench standard):** a task passes iff **every `FAIL_TO_PASS`
test passes AND every `PASS_TO_PASS` test still passes** after the patch applies.
`--grader swebench` needs `[swebench]` + Docker; add `--grader-skip-unavailable`
to leave tasks ungraded (instead of erroring) where Docker is absent. Without a
grader, CODE accuracy stays `None` (ungraded) so it never inflates the score.

> **Windows note:** `swebench` is **Linux-only** (it imports `resource`) and the
> harness runs Linux Docker containers, so CODE grading must run from **WSL**,
> not the Windows-host Python. The offline path and QA grading run fine on
> Windows. On this machine a ready WSL env exists:
>
> ```bash
> # one-time: a WSL venv with memeval + swebench (Docker Desktop WSL integration on)
> python3 -m venv ~/.venvs/swebench
> ~/.venvs/swebench/bin/pip install -e /mnt/c/Users/kenhu/agent-memory-harness/eval "swebench>=4.0"
>
> # run CODE grading from WSL (Docker reachable; pulls per-instance images — slow)
> wsl -d Ubuntu -- ~/.venvs/swebench/bin/python -m memeval.results run \
>     --benchmark swe_bench_cl --model claude-haiku-4-5 --memory \
>     --grader swebench --results /mnt/c/Users/kenhu/agent-memory-harness/results.json
> ```
>
> First grade per instance pulls/builds a multi-GB image; budget time + disk.

## 6. The four metrics

| Metric | Definition | Better |
|---|---|---|
| **Recency** | Is the freshest relevant memory ranked first? | higher |
| **Efficiency** | Memory-token overhead `memory_tokens / total_tokens`, mean over tasks | **lower** (target ≤ 0.10) |
| **Relevancy** | Retrieved items actually relate to the query | higher |
| **Accuracy** | Task correctness (QA match / CODE tests) | higher |

Determinism: no wall-clock enters metric logic; retrieval honors each task's
"as-of" time (never surfaces a future memory). Same inputs → same numbers.

## 7. Read the scoreboard

```bash
python -m memeval.results summary --path ../results.json          # text verdict
python -m memeval.results summary --path ../results.json --json   # machine-readable
```

The same verdict renders live on the **Results** page (computed client-side from
`results.json`). A benchmark is a **win** when `accuracy(treatment) ≥
accuracy(baseline)`, is non-zero, **and** memory overhead ≤ the budget (10% by
default; `--efficiency-budget`). Statuses: `win`, `over_budget` (accuracy clears
the bar but overhead doesn't), `loss`, `incomplete` (missing a role).

**Success criterion:** the hypothesis is **met** when ≥ **2 of 5** benchmarks win
(`--min-wins`). The CLI exits `0` if met, `2` if not.

## 8. Dividing the work (cost & time)

~20 runs (5 benchmarks × 4 configs) is too much for one key. Each captain runs
the benchmark(s) that stress their component, on **their own** API key:

| Benchmark | Captain |
|---|---|
| SWE-Bench-CL | Keith |
| LongMemEval | Ken |
| SWE-ContextBench, ContextBench | Brent |
| MemoryAgentBench | Scott B. |

Cost controls: a hard `--budget-usd` per run (default $10); start with
`--dev-slice 0.1` before the full set; baselines (memory off) in week 1,
treatment (memory on) in week 2. Each captain logs to `results.json` via PR; Ken
aggregates with the scoreboard.

## 9. Run it from GitHub (no local setup)

**Actions → "Benchmark run" → Run workflow.** Free + offline by default
(EchoModel on the fixture). For a paid model the launcher uses **their own**
`ANTHROPIC_API_KEY_<HANDLE>` repo secret (no shared key); the run uploads its
trajectory + `results.json` and opens a PR that adds the row to the Results page.
See [`../collaborate.html`](../collaborate.html).

## 10. Reproduce a published number

1. Note the row's `model`, `memory`, `source`, and `run_id` in `results.json`.
2. Re-run with the same `--benchmark`, model, and `--memory/--no-memory`
   (and `--grader swebench` for CODE).
3. Offline/echo runs reproduce exactly; live-model runs reproduce metric logic
   deterministically given identical model outputs (set the same model + `k`).

## 11. Tracing (Langfuse, optional)

Both run paths (`harness.run` and `agent.run_agent`) mirror each run to
**Langfuse** when keys are present — one trace per task (retrieve / generate
steps) plus a run-level span carrying the four metrics as scores. It is a
**no-op** unless `langfuse` is installed *and* keys are set, so the offline path
is untouched.

```bash
pip install -e ".[langfuse]"
export LANGFUSE_PUBLIC_KEY=pk-... LANGFUSE_SECRET_KEY=sk-...
export LANGFUSE_HOST=https://us.cloud.langfuse.com   # or LANGFUSE_BASE_URL; EU: https://cloud.langfuse.com
python -m memeval.results run --benchmark longmemeval --model claude-haiku-4-5 --memory
```

On this machine the keys are already in the env (Windows + WSL) and set as repo
secrets, so local runs and the **Benchmark run** Action trace automatically; a
fork without `LANGFUSE_*` secrets simply skips tracing.
