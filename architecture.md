# Architecture — Cookbook Memory

> **Technical contract** (the *how & where*). This is the human-readable mirror of
> the frozen `eval/memeval/schema.py` + `protocols.py`. It is the doc that prevents
> **interface drift** — the conflict type tests can't catch. Change only via a
> `[CONTRACT]` PR (see [`plan.md`](plan.md)).

## 1. Components
A coding agent writes memories as it works; a **router** sends each query to the
one backend that answers best; an **offline "dreaming" worker** keeps the store
clean. Four modules over three indexed storage backends. See the diagram on the
site: [`assets/img/architecture.svg`](assets/img/architecture.svg).

1. **Persistence layer (write)** — what/when/where to save; tags each item.
2. **Intelligent router (dispatch)** — classify query → single best backend.
3. **Retrieval orchestrator (read)** — rank by `recency × relevancy`, dedup, return.
4. **Dreaming component (async)** — dedup, conflict resolution, governance, retention.

The **OpenCode memory framework** (`eval/memeval/opencode/`, Keith) is the agent
that consumes all four: it wraps OpenCode's loop as an `AgentAdapter` and exposes
a single `MemoryFramework` (itself a `MemoryStore`) that **routes reads/writes to
Brent's backends and runs Scott's dreaming**. The eval-side seam it plugs into —
`AgentAdapter` + `run_agent` — lives in `eval/memeval/agent.py` (Ken).

## 2. Module boundaries & directory ownership
The single most important section — one owner per path (mirrored in
[`.github/CODEOWNERS`](.github/CODEOWNERS)).

| Path | Owner |
|---|---|
| `eval/memeval/schema.py`, `protocols.py` | **all four (frozen)** |
| `eval/memeval/harness.py`, `models.py`, `cli.py`, `opencode/` | **Keith** |
| `eval/memeval/loaders/`, `metrics.py`, `cost.py`, `trajectory.py`, `agent.py`, `tracing.py`, `results.py`, `tests/` | **Ken** |
| `eval/memeval/stores/`, `router.py` | **Brent** |
| `eval/memeval/dreaming/` | **Scott B.** |
| `*.html`, `assets/`, `project-plan.md` | **Ken** (site) |

## 3. Frozen public interfaces (the contract)
Defined in `eval/memeval/protocols.py` and `schema.py`:

- **`MemoryStore`** — `write(item)`, `get(id)`, `search(query, k) -> list[RetrievedItem]`, `all()`.
- **`ModelAdapter`** — `generate(prompt, **) -> (text, tokens_in, tokens_out)` plus `name`, `price_in`, `price_out`.
- **`Loader`** — `load(path_or_id, **) -> list[Task]`.
- **Data model** — `Task`, `Session`, `MemoryItem`, `RetrievedItem`, `TrajectoryStep`, `Trajectory`, `ModelConfig`, `Metrics`, `RunResult`, `Benchmark`, `TaskKind`.

**Invariants not captured by the signatures** (the real contract — easy to violate silently):
- `search` returns items sorted by **descending score**, with `rank` set (0 = best),
  and **must** set `RetrievedItem.tokens` (the efficiency metric depends on it).
- Retrieval must respect the query's "as-of" time — never surface memories from the future.
- The offline path imports **no** third-party package at module top level; heavy
  deps (`anthropic`, `datasets`, `numpy`, …) are imported lazily inside the function that needs them.

## 4. How components talk
`loaders → list[Task] → harness.run(benchmark, model, memory) → MemoryStore / ModelAdapter → metrics + cost`.
One entry point — `harness.run()` — drives all **five** benchmarks. `InMemoryStore`
and `EchoModel` are the reference stubs that let every other module be built and
tested independently.

For the real coding agent, the multi-step sibling
`agent.run_agent(benchmark, agent, memory, store=…)` drives an `AgentAdapter`
loop. Keith's `OpenCodeAgent` is that adapter, and the `store=` it receives is his
`MemoryFramework` — backed by **Brent's** `stores/` + `router.py` and consolidated
by **Scott's** `dreaming/`. So Keith's framework *depends on* Brent's and Scott's
components landing first (see the dependency graph in [`plan.md`](plan.md)).

## 5. The freeze
`schema.py` + `protocols.py` are **frozen as of Day 3**, standard-library only.
Once frozen, Brent builds backends, Ken builds loaders/adapters, and Scott B.'s
dreaming reads `store.all()` — all against signatures that won't move under them.

## 6. Extension points (add without touching frozen files)
Add a backend / loader / model adapter by implementing the relevant
`typing.Protocol` in **your** directory and registering it — no contract edit
required. That is what makes the freeze a feature, not a cage.

---
Product rationale: [`prd.md`](prd.md) · Ownership, dependencies, change process: [`plan.md`](plan.md).
