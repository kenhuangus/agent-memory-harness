---
id: ADR-storage-012
domain: storage
title: Query expansion (multi-query recall) — opt-in, lifts the accuracy profile, regresses fusion; off by default
status: Accepted
date: 2026-06-28
contract: false
supersedes: none
superseded_by: none
owner: cookbook-improvement-loop
origin: suggestion1.md idea 2 (MRAgent review); cookbook-improvement-loop Tier-1 gate
---

# ADR-storage-012: Query expansion (multi-query recall) — opt-in, accuracy-profile lift

**Status:** Accepted · **Date:** 2026-06-28 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context

A single query phrasing under-recalls: proper nouns, plurals, and tense/form variants a real
match uses but the query doesn't are missed by both vector and lexical retrieval. MRAgent's
fix (`suggestion1.md` idea 2) makes alternative phrasings mandatory — extract the query's keys
plus synonyms / different tense / different form, and retrieve for all of them.

## Decision

Add `ExpandedQueryStore` ([`memeval/stores/query_expand.py`](../../eval/memeval/stores/query_expand.py)):
it runs the original query plus a few LLM-generated alternative phrasings through the inner store
and merges candidates by max score. Wired into the recall seam
([`build_store`](../../plugin/cookbook_memory/core/contract.py)) **behind an env flag, off by default**:

- `$MEMORY_QUERY_EXPAND=llm` — alternatives from the dream model (paid).
- `$MEMORY_QUERY_EXPAND=mock` — offline morphological variants (mechanism only; tests).
- unset / `none` — no expansion (default).
- `$MEMORY_QUERY_EXPAND_N` — max alternatives (default 4).

The Tier-1 gate gains `--expand {none,llm,mock}` to measure it. Lazy import — offline default untouched.

## Evidence (Tier-1 gate)

Fixture `8c48b84` (158 items, 18 real queries), judge pinned `gpt-4o-mini`, k=5, expansion model
`deepseek-chat-v3.1`:

| profile | baseline p@5 | expand=llm p@5 | Δ | useful-hit-rate | verdict |
|---|---|---|---|---|---|
| accuracy | 0.633 | **0.711** | +0.078 | 0.889 → 0.944 | PASS (≥ base+0.05) |
| fusion | 0.444 | 0.422 | −0.022 | 0.889 | FAIL (regressed) |

**Mixed, profile-dependent.** It clears the bar on `accuracy` (the production default when a Voyage
key is present — the benchmark config) but slightly regresses `fusion` (the offline default). It also
adds one LLM call per recall. **Tier-2 neutral by construction** (read-time; store unchanged).

## Consequences

- Approved as an **opt-in, accuracy-profile** feature: default behavior is byte-identical (no
  `$MEMORY_QUERY_EXPAND` → no import, no LLM call). Recommended only with the accuracy profile;
  do **not** enable with fusion (it regresses there).
- Strictly weaker than the reranker ([`ADR-storage-011`](ADR-storage-011-two-stage-retrieve-rerank.md)):
  +0.078 vs +0.111 on accuracy, a fusion regression vs a fusion gain, and a per-recall LLM cost the
  reranker doesn't incur. If only one is enabled, prefer the reranker.
- Not Tier-3-confirmed. Tier-1/2 are necessary, not sufficient.
