---
id: ADR-storage-011
domain: storage
title: Two-stage retrieve→rerank — opt-in Voyage cross-encoder over the top ~50 lifts Tier-1 precision@5; off by default
status: Accepted
date: 2026-06-28
contract: false
supersedes: none
superseded_by: none
owner: cookbook-improvement-loop
origin: suggestion1.md idea 1 (MRAgent review); cookbook-improvement-loop Tier-1 gate
---

# ADR-storage-011: Two-stage retrieve→rerank — opt-in Voyage cross-encoder over the top ~50

**Status:** Accepted · **Date:** 2026-06-28 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context

Our SWE-Bench-CL findings showed the bottleneck moved from memory *availability* to
retrieval *quality & ranking*: memory is recalled on nearly every task but doesn't
convert to a solve. The recall path was **single-stage** — the cheap retriever's
similarity order was the final order. A review of MRAgent (*"Memory is Reconstructed,
Not Retrieved"*) reinforced the standard fix: over-fetch cheap candidates, then re-score
them with a stronger cross-encoder (`suggestion1.md`, idea 1).

The reranker component already existed in the tree
([`memeval/stores/rerankers.py`](../../eval/memeval/stores/rerankers.py): `RerankedStore`,
`VoyageReranker`, `MockReranker`, owner @bgibson1618) but was **not wired into the recall
path** — the production seam ([`build_store`](../../plugin/cookbook_memory/core/contract.py))
returned a `RouterStore` with no rerank, and the Tier-1 gate exercised a plain rebuilt
store. The component shipped the mechanism; nothing measured or enabled its lift.

## Decision

Wire the reranker into the recall path **behind an env flag, off by default**:

- [`build_store`](../../plugin/cookbook_memory/core/contract.py) honors `$MEMORY_RERANK`
  (`voyage` | `mock` | unset/`none`) and `$MEMORY_RERANK_TOP_N` (default 50): when set, it
  wraps the routed `RouterStore` in `RerankedStore`, preserving the observability attrs the
  plugin reads (`profile_name`, `recall_min_score`). Lazy import — the offline default never
  touches the reranker module.
- [`recall_precision_gate.py`](../../eval/tools/recall_precision_gate.py) gains `--rerank
  {none,voyage,mock}`, mirroring `$MEMORY_RERANK`, so the Tier-1 gate can measure the lift.

Default stays **no rerank** (the offline lexical reranker only demonstrates the mechanism;
the real lift is the paid Voyage cross-encoder — the D019/D020 captained-run lesson).

## Evidence (Tier-1 gate)

Fixture: `results/vsympy_sympy_sequence-plugin-dreamed-8c48b84-1` (158 items, 18 real recall
queries), judge pinned `openai/gpt-4o-mini`, k=5.

| profile | baseline p@5 | rerank=voyage p@5 | Δ | useful-hit-rate | verdict |
|---|---|---|---|---|---|
| fusion | 0.444 | **0.678** | +0.234 | 0.889 → 0.944 | PASS (≥ base+0.05) |
| accuracy | 0.633 | **0.744** | +0.111 | 0.889 → 1.0 | PASS (≥ base+0.05) |

Both profiles clear the Tier-1 bar (useful-hit-rate ≥ 0.60 **and** precision@5 ≥ baseline +
0.05). MRR also rises (fusion 0.75 → 0.875). **Tier-2 is neutral by construction** — reranking
is read-time only and does not change what is stored, so consolidation quality is unaffected.

## Consequences

- A real retrieval-quality lever, opt-in and measured, with the default behavior byte-identical
  (no `$MEMORY_RERANK` → no import, no network, no change).
- Cost: one Voyage rerank call per recall when enabled; bounded by `$MEMORY_RERANK_TOP_N`.
- Not yet confirmed at Tier-3 (per-CLI solve-rate). Tier-1/2 are necessary, not sufficient;
  a Tier-3 run is the next step before enabling by default in any benchmark arm.
