# AI Agent Memory Harness — Project Plan

**A persistent memory harness for long-running coding agents.**
Team of 4 · Two-week sprint · Model-agnostic · Validated on public benchmarks

---

## 1. The problem

Long-running coding agents are effectively **stateless across sessions**. Every new session starts
cold: the agent has forgotten the fixes it already made, the conventions of the codebase, the
dead-ends it already explored, and the environment "gotchas" it already learned. The same mistakes
get repeated and the same context gets re-derived at cost.

This hits **smaller, cheaper models hardest**. A frontier model wins partly because it can hold more
in its head at once; a smaller model cannot, so it falls behind on long-horizon work. Today the only
reliable way to get good long-horizon performance is to pay for the largest model.

There is **no standard, model-agnostic memory layer** that answers the four hard questions well:

- **What** to remember (signal vs. noise), and **when** to write it.
- **Where** to store it (different memory shapes want different stores).
- **How** to retrieve the right thing **fast**, without flooding the context window.
- **How** to keep the store **consistent** over time — de-duplicated, contradiction-free, and pruned.

**The opportunity:** if an agent can reliably save, retrieve, and reconcile what it learns, a cheaper
model should be able to *borrow* the long-horizon advantage of a bigger one.

---

## 2. Hypothesis & success criteria

> **Hypothesis.** With the memory harness, **Haiku can close the gap to Opus 4.8** (which runs without
> memory) on established memory benchmarks.

**Success criterion.** On **at least two of the four** benchmarks, *Haiku + harness* beats the
*Opus 4.8 no-memory* baseline across the four metrics — **without** blowing the efficiency budget
(memory adds **< ~10%** context-token overhead).

The four metrics we measure:

| Metric | Definition |
|---|---|
| **Recency** | Temporal distance of retrieved memory to the current task — is the freshest relevant item ranked first? |
| **Efficiency** | Tokens spent per retrieval; target under ~10% overhead. |
| **Relevancy** | Semantic similarity of retrieved items to the query — only pull memories that truly relate. |
| **Accuracy** | Correctness of agent output, memory-on vs. memory-off. |

---

## 3. Technical approach

A **pluggable memory harness** with four modules sitting over three storage backends, integrated into
the open-source **OpenCode** agent and evaluated against public benchmarks.

### Four modules

1. **Persistence layer (write).** Decides what/when/where to save. Tags each item with a timestamp,
   relevancy score, session tag and provenance; handles versioning and dedup hints.
2. **Intelligent router (dispatch).** Instead of fanning out to all stores, it classifies each query
   and routes it to the *single best* backend. Rule-based to start, with a learned-classifier upgrade
   path (same signature, no caller changes).
3. **Retrieval orchestrator (read).** A unified query API: calls the router, ranks results by
   `recency × relevancy`, de-duplicates, and returns a tight context.
4. **Zooming component (async curate).** Runs offline when the agent is idle. Four jobs:
   de-duplicate across backends, resolve contradictions, maintain session governance
   (must-know / must-do / blacklist), and apply selective retention + pruning.

### Three storage backends (each with its own index)

| Backend | Best for | Index |
|---|---|---|
| **Markdown + YAML** | Human-readable notes, literal recall | Inverted keyword map (keyword → file path) |
| **SQLite + vectors** | Semantic similarity search | Dense ANN (HNSW / FAISS) |
| **Graph store** | Relationships & conflicts | Typed traversal (node / edge type) |

### Evaluation

Validated against **existing public benchmarks only** — no home-grown evals. A controlled grid per
benchmark runs *Haiku + harness*, *Haiku no-memory*, *Opus 4.8 no-memory*, and *Sonnet no-memory*,
holding prompts/tools/task order fixed and logging every trajectory for reproducibility.

---

## 4. Scope

### In scope

- The memory harness: all four modules, end to end.
- Three storage adapters (Markdown, SQLite-vector, Graph) behind one interface, each indexed.
- The intelligent router (rule-based; learned-classifier hook).
- The async zooming component: dedup, conflict resolution, session governance, retention/pruning.
- OpenCode integration for systematic memory write/read on each agent step.
- Evaluation harness over the four benchmarks; baselines for Haiku, Opus 4.8 and Sonnet.
- Metric definitions, a reproducible protocol, and a results dashboard.

### Out of scope (non-goals)

- **Building our own benchmark or dataset** — we leverage existing public ones.
- **Training or fine-tuning** any model.
- Production hardening: multi-tenant infra, auth, SLAs, a polished UI.
- Distributed / horizontally-scaled deployment — single-node is sufficient for the sprint.
- Validating non-coding agents — the design aims to generalize, but only coding agents are tested now.

---

## 5. Team & ownership — who owns what

Four parallel workstreams, locked behind a shared storage interface that is **frozen on Day 3**.

| Area / deliverable | Owner | Supporting |
|---|---|---|
| Storage interface & memory-item schema | **P1** | P3 |
| Persistence layer (write path, tagging, versioning) | **P1** | — |
| Retrieval orchestrator (rank · dedup · return) | **P1** | P3 |
| OpenCode integration (write/read on each step) | **P1** | — |
| Three storage backends + per-backend indexes | **P3** | P1 |
| Intelligent router (rules → learned) | **P3** | P1 |
| Backend performance testing | **P3** | P2 |
| Zooming worker (dedup, conflict, governance, retention) | **P4** | P1, P3 |
| Memory semantics ("what is good memory") | **P4** | P2 |
| Datasets, loaders & trajectory logging | **P2** | — |
| Metric definitions & evaluation protocol | **P2** | P4 |
| Baselines + results dashboard | **P2** | — |
| Final end-to-end integration | **All** | — |

**Roles in one line each**

- **P1 — Harness Architecture & OpenCode Integration.** Owns the critical path: the contracts everyone builds against.
- **P2 — Benchmarking & Evaluation.** Owns experimental design, metrics, and reproducibility.
- **P3 — Storage Implementation & Router.** Turns the abstraction into three fast backends + the dispatch layer.
- **P4 — Zooming Component & Memory Governance.** Owns the offline engine that keeps memory clean and trustworthy.

---

## 6. Two-week timeline

### Person 1 — Harness Architecture & OpenCode Integration
- **Week 1:** Design the three-module abstraction & storage interface (swappable backends). Build the
  persistence layer: write logic, versioning, metadata tagging. Instrument OpenCode to write memory on each step.
- **Week 2:** Build the retrieval orchestrator (rank → dedup → return). Wire in P3's router; run an end-to-end task.

### Person 2 — Benchmarking & Evaluation
- **Week 1:** Download & parse MemoryAgentBench, LongMemEval, SWE-ContextBench, SWE-Bench-CL. Build data
  loaders + trajectory logging. Define the four metrics mathematically.
- **Week 2:** Run baselines (Haiku no-memory, Opus 4.8 no-memory). Build the statistical-testing framework and
  results dashboard; document the protocol.

### Person 3 — Storage Implementation & Router
- **Week 1:** SQLite + vector pipeline (embedding model, HNSW/FAISS index). Markdown store with YAML frontmatter +
  inverted index. Graph store schema (Neo4j) with typed traversal index.
- **Week 2:** Adapters for all three backends behind P1's interface. Implement the router; performance-test each backend.

### Person 4 — Zooming Component & Memory Governance
- **Week 1:** Deduplication logic (exact / semantic / near-dup). Conflict detection + reconciliation rules
  (recency, confidence, source). Session-filter & blacklist semantics.
- **Week 2:** Async offline scheduler; must-know / must-do extraction. Retention & pruning; observability logs;
  integration with P1 & P3.

### Integration — all four, Day 10
Full end-to-end run: Haiku + harness on ≥ 1 benchmark, capturing all four metrics against baselines.

---

## 7. Milestones

| Day | Milestone |
|---|---|
| **D3** | **Contract freeze** — storage interface + memory-item schema locked; metric definitions agreed. |
| **D5** | **End of week 1** — three backends write & read in isolation; datasets parsed; persistence writing live. |
| **D8** | **Integration start** — router + orchestrator connected; zooming worker running against real stores. |
| **D10** | **Ship** — full E2E; four metrics captured vs. baselines. |

---

## 8. Dependencies & key risk

- **P1 → P3:** the storage interface must be frozen by end of D3 so adapters can build against it.
- **P3 → P1:** the router lands before the orchestrator can route in week 2.
- **P2 → all:** datasets + logging ready by D5 so baselines can start D6.
- **P1 + P3 → P4:** the zooming worker integrates once real storage exists (D8+).

> **Top risk is semantic, not structural:** defining *what counts as "good memory."* P2 and P4 must align
> on memory semantics in week 1, or the harness will store everything and retrieve nothing useful.

---

## 9. Benchmarks & metrics

| Benchmark | Primary metrics | Link |
|---|---|---|
| **MemoryAgentBench** | Relevancy, Accuracy, conflict resolution | arXiv 2507.05257 |
| **LongMemEval** | Recency, Accuracy, temporal reasoning | arXiv 2410.10813 |
| **SWE-ContextBench** | Efficiency, Relevancy, Accuracy | arXiv 2602.08316 |
| **SWE-Bench-CL** | Relevancy, Accuracy, continual learning | arXiv 2507.00014 |

Full descriptions, dataset links, and the metric-mapping matrix live on the project site's
**Benchmarks** page.

---

## 10. Deliverables

- A working, model-agnostic memory harness (4 modules) integrated with OpenCode.
- Three indexed storage backends behind one interface.
- The async zooming component with governance & pruning.
- A reproducible evaluation harness + baseline numbers on four benchmarks.
- A results scoreboard testing the central hypothesis.
- This project site (GitHub Pages) documenting all of the above.
