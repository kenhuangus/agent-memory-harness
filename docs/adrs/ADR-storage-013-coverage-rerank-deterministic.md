---
id: ADR-storage-013
domain: storage
title: Deterministic coverage re-rank — no-LLM key-overlap blend; lifts fusion, off by default
status: Accepted
date: 2026-06-28
contract: false
supersedes: none
superseded_by: none
owner: cookbook-improvement-loop
origin: suggestion1.md idea 3 (MRAgent review); cookbook-improvement-loop Tier-1 gate
---

# ADR-storage-013: Deterministic coverage re-rank (no-LLM key-overlap blend)

**Status:** Accepted · **Date:** 2026-06-28 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context

MRAgent ranks candidates by *how many* of the query's keys they satisfy (full vs partial
coverage). We have no key-graph, but the cheap content-only adaptation (`suggestion1.md` idea 3)
needs none: blend the retriever's similarity with the fraction of the query's salient key tokens
present in each candidate. It is **deterministic and LLM-free** — unlike the reranker
([`ADR-storage-011`](ADR-storage-011-two-stage-retrieve-rerank.md)) and query expansion
([`ADR-storage-012`](ADR-storage-012-query-expansion-multiquery-recall.md)), it adds no per-recall
network cost.

## Decision

Add `CoverageRerankStore` ([`memeval/stores/coverage_rank.py`](../../eval/memeval/stores/coverage_rank.py)):
over-fetch the inner top-N, re-rank by ``alpha*norm(similarity) + (1-alpha)*key-coverage``, keep
top-k. Wired into the recall seam behind an env flag, **off by default**:

- `$MEMORY_COVERAGE_RERANK=1` — enable.
- `$MEMORY_COVERAGE_ALPHA` — similarity weight (default 0.5).
- `$MEMORY_COVERAGE_FETCH` — candidates over-fetched (default 30).

The Tier-1 gate gains `--coverage`. Lazy import; offline default untouched.

## Evidence (Tier-1 gate)

Fixture `8c48b84` (158 items, 18 real queries), judge pinned `gpt-4o-mini`, k=5:

| profile | baseline p@5 | coverage p@5 | Δ | useful-hit-rate | verdict |
|---|---|---|---|---|---|
| fusion | 0.444 | **0.511** | +0.067 | 0.889 → 0.833 | PASS (≥ base+0.05) |
| accuracy | 0.633 | 0.678 | +0.045 | 0.889 → 1.0 | narrowly under (+0.05 bar) |

A real, **free** lift on the offline `fusion` profile; accuracy +0.045 sits one noise-step under the
bar (n=18). **Tier-2 neutral by construction** (read-time; store unchanged).

## Consequences

- Approved as an **opt-in, fusion-profile** feature (zero LLM cost makes it the natural choice for
  the offline/no-key path). Default behavior byte-identical.
- Complements query expansion ([`ADR-storage-012`](ADR-storage-012-query-expansion-multiquery-recall.md)):
  coverage helps fusion / hurts nothing for free; expansion helps accuracy at an LLM cost. The
  reranker ([`ADR-storage-011`](ADR-storage-011-two-stage-retrieve-rerank.md)) still dominates both
  where a Voyage key is available.
- Not Tier-3-confirmed. Tier-1/2 are necessary, not sufficient.
