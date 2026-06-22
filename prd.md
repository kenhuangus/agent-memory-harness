# PRD — Cookbook Memory

> **Product contract** (the *what & why*). The least volatile of the three contracts.
> Changing **Goals** or **Success metrics** requires sign-off from all four owners.
> Codename: **Cookbook Memory** · descriptive name: *AI Agent Memory Harness*.

## 1. Problem
Long-running coding agents are stateless across sessions. There is **no standard,
model-agnostic layer** that decides **what** to remember, **where** to store it,
**how** to retrieve it fast, and **how** to keep it consistent over time. Without
it, the only reliable way to get good long-horizon performance is to pay for the
largest model.

## 2. Goals
- A pluggable, model-agnostic **memory harness** for long-running coding agents.
- **Hypothesis:** Haiku **+ the harness** can beat **Opus 4.8 with no memory** on
  public memory benchmarks.

## 3. Non-goals (out of scope)
- Building our own benchmark or dataset (we use public ones).
- Training or fine-tuning any model.
- Production hardening: multi-tenant infra, auth, SLAs, polished UI.
- Distributed / scaled deployment (single-node is enough for the sprint).
- Validating non-coding agents (design generalizes; not tested now).

## 4. Success metrics (frozen acceptance criteria)
On **≥ 2 of the 5 benchmarks**, *Haiku + harness* beats the *Opus 4.8 no-memory*
baseline across the four metrics, **without** > ~10% memory-token overhead.

| Metric | Definition |
|---|---|
| Recency | Is the freshest relevant memory ranked first? |
| Efficiency | Memory tokens ÷ total tokens; target < ~10%. |
| Relevancy | Retrieved items actually relate to the query. |
| Accuracy | Task correctness, memory-on vs. memory-off. |

These map 1:1 to `eval/memeval/metrics.py`.

## 5. Users & primary use case
Long-running coding agents (e.g. OpenCode) that work across many sessions on a
codebase and benefit from persistent, self-curating memory.

## 6. Constraints
- Model-agnostic; the contract layer (`schema.py` + `protocols.py`) is **stdlib-only**.
- Python 3.11+ (developed on 3.13).
- The offline evaluation path is reproducible with **zero required dependencies**.

## 7. Must-have decisions (resolved)
These three were the open risks; each now has a committed default with a
documented fallback. They are requirements for a real (paid) production run,
not the offline path.

### 7.1 Embedding model + reranker for the vector backend
- **Embeddings — default: Voyage AI `voyage-3-large`** (1024-d, top retrieval
  quality / cost on code + long-context; matches our coding-agent use case).
  Budget alternative: `voyage-3-lite`.
- **Reranker — default: Voyage `rerank-2.5`**; Cohere `rerank-3.5` is an
  equivalent drop-in. Rerank the top ~50 ANN hits down to top-k before they
  enter the prompt.
- **Open-source fallback (no external API / air-gapped): `BAAI/bge-m3`
  embeddings + `BAAI/bge-reranker-v2-m3`.** Self-hostable, keeps the offline
  guarantee for teams that cannot call a hosted embedding API.
- Wiring: behind the `MemoryStore` protocol — the embedder/reranker are
  injected, so swapping providers does not touch benchmark or harness code.

### 7.2 Real CODE scoring (patch-apply / test-run) per benchmark
- **Default: host-local test execution (`LocalExecGrader`), Docker-free.** CODE
  runs as a real coding agent in a working checkout (`--code-mode agentic`); the
  harness captures `git diff` as the prediction, applies the **gold `test_patch`**
  itself (never the agent — the trust boundary), and runs the project's tests in a
  per-task venv. A task PASSES only when **every `FAIL_TO_PASS` test now passes AND
  every `PASS_TO_PASS` test still passes** (SWE-bench resolved rule).
- **Honesty rule:** the grader reports `None` (UNGRADED, excluded from accuracy)
  whenever the env can't be built — never a fake `False`. Local-exec is
  host-dependent and partial-coverage, so it is NOT comparable to a containerized
  SWE-bench leaderboard. Docker and the `swebench` package are removed entirely
  (see `docs/adrs/ADR-eval-002`).
- Wiring: exposed as a per-benchmark `grader` for the SWE coding benchmarks
  (`swe_contextbench`, `swe_bench_cl`). `contextbench` is retrieval-only (native
  recall/precision/F1 over gold spans, no test execution). The offline path stays
  string/overlap-based so the zero-dependency guarantee holds.

### 7.3 Live pricing for the cost tracker
Confirmed against the Anthropic price list (USD per **million** tokens) and
committed in `eval/memeval/cost.py` (`PRICING`):

| Model            | Input $/Mtok | Output $/Mtok |
|------------------|-------------:|--------------:|
| Haiku 4.5        |         1.00 |          5.00 |
| Sonnet 4.6       |         3.00 |         15.00 |
| Opus 4.8         |         5.00 |         25.00 |

Default per-run budget is **$10** (`DEFAULT_BUDGET_USD`); override with
`--budget-usd` (set `<=0` to disable the cap). Re-confirm list prices before a
large paid run.

---
See [`architecture.md`](architecture.md) for the technical contract and
[`plan.md`](plan.md) for ownership, the dependency graph, and the change process.
