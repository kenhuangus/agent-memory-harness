---
id: ADR-eval-007
domain: eval
title: Benchmark suite re-scoped to two in-scope benchmarks (swe_bench_cl primary + VISTA 2nd); the four memory benches kept available but de-scoped to legacy
status: Accepted
date: 2026-06-24
contract: false
supersedes: none
superseded_by: none
owner: Ken (P2)
origin: user directive
---

# ADR-eval-007: Two-benchmark suite — swe_bench_cl (primary) + VISTA (2nd); legacy benches kept available

**Status:** Accepted · **Date:** 2026-06-24 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
The harness historically positioned **five** public benchmarks
(`memoryagentbench`, `longmemeval`, `swe_contextbench`, `swe_bench_cl`,
`contextbench`) as a co-equal suite. VISTA Bench was then added as an additional
registered benchmark (loader + native evaluator + RSI safety gate; see the
`feat/vista-leverage` work) exercising a dimension none of the five covered:
long-horizon foresight × safety and **memory poisoning / adaptation**.

The project now positions its evaluation around the two benchmarks that carry
the headline story, rather than spreading attention across six.

## Decision
The benchmark suite is positioned around **two in-scope benchmarks**:

1. **`swe_bench_cl`** — the **primary** CODE / continual-learning benchmark.
2. **`vista`** — the **2nd** benchmark: foresight × safety + memory poisoning /
   adaptation (corpus CC-BY-4.0).

The four original memory benchmarks — `memoryagentbench`, `longmemeval`,
`swe_contextbench`, `contextbench` — are **de-scoped to legacy / non-primary**.

This is a **documentation-level** scoping decision only:

* **No benchmark code is removed.** All four legacy loaders, native evaluators,
  and their tests remain in the tree.
* **All benchmarks stay selectable** via `--benchmark <id>` and remain in the
  loader / native-evaluator registries. Nothing is dropped from the runnable
  registry.
* Only the *positioning* in prose (READMEs, PRD, plan, schema docstring) changes:
  the headline suite is `swe_bench_cl` + `vista`; the other four are labeled
  legacy.

## Consequences
* New work, headline metrics, and reporting lead with `swe_bench_cl` + `vista`.
* Existing runs and tooling against the legacy benches keep working unchanged
  (they are still registered and tested).
* The full offline test suite stays green; the legacy benches' tests are
  untouched.
* If a future decision wants to fully retire a legacy bench, that is a separate
  ADR — this one deliberately keeps them runnable.
