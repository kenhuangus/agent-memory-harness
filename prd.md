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

## 7. Open questions (parking lot)
- Final embedding model + reranker for the vector backend.
- Real CODE scoring (patch-apply / test-run) wiring per benchmark.
- Live pricing for the cost tracker (placeholders today).

---
See [`architecture.md`](architecture.md) for the technical contract and
[`plan.md`](plan.md) for ownership, the dependency graph, and the change process.
