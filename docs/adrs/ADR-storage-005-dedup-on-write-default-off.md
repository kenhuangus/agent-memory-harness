---
id: ADR-storage-005
domain: storage
title: Dedup-on-write ships default-OFF — offline lexical similarity cannot safely merge (false-merge = silent data loss)
status: Accepted
date: 2026-06-23
contract: false
supersedes: none
superseded_by: none
owner: Brent (P3)
origin: DECISION_LOG D024 (capstone-workspace); PR #57
---

# ADR-storage-005: Dedup-on-write ships default-OFF — offline lexical similarity cannot safely merge

**Status:** Accepted · **Date:** 2026-06-23 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context

The write layer is asked (ADR-P2/P4; [`ADR-storage-001`](ADR-storage-001-orchestrator-in-process-library.md) Open items) to **dedup-on-write**: a near-duplicate `remember` should MERGE into the existing memory and return its id rather than create a second copy. PR #57 added the mechanism: `Router.write(item) -> WriteReceipt` runs a *dedup-resolve* step (find a near-duplicate in the dedup backend; if found, reuse its id with **newer-content-wins**, `version+1`) before routing and persisting.

The open question was the **default and the threshold**. This is the only "dedup" anywhere near the write path; note it is a **different mechanism** from the dreaming/orchestrator consolidation dedup (architecture §1.3/§1.4/§7.3), which is the async whole-store pass — a reader must not conflate them.

The risk is asymmetric: a **false merge collapses two distinct memories = silent data loss**, irreversible and invisible. So the threshold can't be picked by feel; it was calibrated.

## Options considered

- **On-by-default with a high lexical threshold.** The obvious choice. **Rejected by calibration (D024):** over 17 blind cases (9 near-dup "merge" + 8 distinct-but-similar "no_merge" traps), the offline char-n-gram similarity of a reworded *duplicate* (0.35–0.75) **overlaps** that of a *distinct* memory (0.21–0.82) — a distinct "read timeout 5s" vs "write timeout 30s" pair scores **0.824**, higher than every real duplicate. No threshold separates them; the zero-false-merge threshold catches **0/9** real dups. A high threshold doesn't help — a future one-word-different distinct pair would exceed it and false-merge.
- **On-by-default, accept some false merges.** Rejected: false-merge = data loss is the one outcome the persistence layer must never silently produce.
- **Default OFF, gate the mechanism to a real semantic embedder (chosen).** Ship the dedup-resolve machinery but keep it off until same-fact vs different-fact actually separate — which they do under a real embedder (the D020 story).

## Decision

**`dedup` defaults OFF** (`RouterConfig.dedup = False`, `dedup_threshold = 0.92`, `dedup_backend = VECTORS`). `Router.write(item, *, dedup=None)` builds the receipt and routes via `route_write`; the dedup-resolve step runs **only** when `dedup` is enabled (per-call override or config). The mechanism is **gated to a real semantic embedder** (the paid path) where char-n-gram lexical similarity is replaced by semantic similarity that can tell a reworded duplicate from a distinct-but-similar fact. The merge path copies via `dataclasses.replace` (the caller's item is never mutated).

## Rationale

The calibration **before** choosing a default (same discipline as D021/D022/D023) prevented shipping a data-loss risk that *looks* safe. Char-trigram similarity ≠ same-fact: two distinct facts differing by one word can look **more** similar than a reworded duplicate, so any offline auto-merge would eventually destroy a real memory. Shipping the mechanism but defaulting it OFF means the capability is ready the instant a real embedder is wired, and the eval is the durable proof of *why* it's off — not a TODO, a measured safety call.

## Tradeoffs & risks

- **Duplicate memories accumulate on the offline path.** With dedup off, repeated near-identical `remember`s create multiple copies. Accepted: duplication is recoverable (the dreaming consolidation pass can merge async); silent data loss is not. The write-path budget bounds *retrieval* tokens, and `route()` returns a de-duplicated top-k regardless.
- **The knob is a foot-gun if enabled offline.** Turning `dedup=True` with the offline embedder reintroduces the false-merge risk. Mitigation: it is OFF by default and documented as real-embedder-gated; the eval demonstrates the danger (a permissive threshold false-merges a distinct pair).
- **Two dedup mechanisms with the same name.** Write-path dedup (this) vs dreaming consolidation dedup (async). A reader of architecture.md sees only the latter and could assume write-path dedup is active. Doc-reconciliation owed (architecture has no `Router.write`/`WriteReceipt`/write-path-dedup/"off by default" statement).

## Consequences for the build

- **Policy:** `dedup` is OFF in every default profile. Enabling it requires a real semantic embedder; do NOT enable offline. Newer-content-wins + `version+1` is the merge semantics when on.
- **Affected files:** [`eval/memeval/router.py`](../../eval/memeval/router.py) (`WriteReceipt` :487, `Router.write(dedup=...)` :1041 with the dedup-resolve step, `_find_duplicate`, `RouterConfig.dedup`/`dedup_threshold`/`dedup_backend`).
- **Cross-links:** the write-routing this composes with is [`ADR-storage-004`](ADR-storage-004-router-owns-write-path-routerstore-seam.md); the real-embedder semantic separation that *unlocks* dedup is the D020 result; version-highest-wins (the eventual merge primitive) needs protocol `delete` ([`ADR-storage-008`](ADR-storage-008-cross-backend-delete-fan-out.md)).
- **Doc-reconciliation owed:** add a one-line write-path-dedup note to architecture.md distinguishing it from the dreaming dedup, stating it ships OFF and why.
