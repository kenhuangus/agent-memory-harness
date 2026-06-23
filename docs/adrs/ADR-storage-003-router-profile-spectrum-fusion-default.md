---
id: ADR-storage-003
domain: storage
title: The router is a profile-driven speed↔accuracy spectrum; the shipped default is cross-backend fan-out-and-fuse
status: Accepted
date: 2026-06-23
contract: false
supersedes: none
superseded_by: none
owner: Brent (P3)
origin: DECISION_LOG D008/D015/D016/D027/D028 (capstone-workspace)
---

# ADR-storage-003: The router is a profile-driven speed↔accuracy spectrum; the shipped default is cross-backend fan-out-and-fuse

**Status:** Accepted · **Date:** 2026-06-23 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context

[`architecture.md`](../../architecture.md) frames module 2 as *"classify query → single best backend"* — one retrieval, the cheapest possible read, the efficiency thesis incarnate. [`ADR-storage-001`](ADR-storage-001-orchestrator-in-process-library.md) and the early router decisions ([D010] scored-signal classifier, [D015] *rejected* fusion-by-default) all assumed **single-route is the front door**.

Three things moved since:

1. **A team mandate (D016, 2026-06-20):** the router should ship **multiple named profiles — some optimizing for speed, some for accuracy** — modeled as a `RouterConfig` seam (`{classifier, embedder, gate thresholds, cascade on/off, consult-2/RRF on/off, k}`). Single-route became *the speed end of a spectrum*, not the only mode.
2. **The cascade (D008):** a `_GraphVectorCascade` (graph→vector fall-through with an exact-anchor gate) that engages for GRAPH-classified queries when both backends are registered — the *accuracy* end's first occupant.
3. **Fusion was re-opened (D027):** [D015] scoped out fusion *as the default*, **not fusion as a config**. The `Consult2Config` seam (reserved since D016) was wired into a real `_FusionRetriever` (fan out to every backend → merge by RRF *or* score-normalization → top-k). PR #68.

The decisive change is the **default-selection rule in the live plugin**. [`plugin/cookbook_memory/core/contract.py`](../../plugin/cookbook_memory/core/contract.py) `build_store()` picks a profile from the environment: `MEMORY_PROFILE` if set, else `accuracy` when `VOYAGE_API_KEY` is present, else **`fusion`**. So with no key (the offline/CI/default install) the router ships **fusion — fan-out across every registered backend** — and with a key it ships **accuracy (the graph→vector cascade)**. The true single-route v1 (`speed`) is *never auto-selected*; it is opt-in only. `Router.route()` returns the `_FusionRetriever` whenever `consult2.enabled`, taking **precedence over the cascade**.

This **inverts** the architecture doc's core framing: the documented behavior is single-best-backend; the *shipped default* behavior is the opposite — fan out and fuse.

## Options considered

- **Keep single-route the hard default; fusion/cascade strictly opt-in.** What the arch doc still implies. Rejected as the *default* because (a) the team mandated a configurable spectrum, and (b) on the offline path with no real embedder, no single backend dominates, so a fan-out default is the recall-safe choice when the cheap embedder is all we have.
- **Make fusion the universal default (always fan out).** Rejected by measurement: **D028 (captained)** showed that when one backend *dominates* — a real `voyage-3-large` vectors backend recovering recall@5 1.000 — fusing it with weaker lexical backends **dilutes** it (fusion 0.900 < vectors-alone 1.000) at a fixed top-k. So fusion must NOT be the default *when a strong embedder is present*.
- **Profile-driven default keyed on capability (chosen):** offline/no-key → `fusion` (no dominant backend, fan-out is recall-safe); with a real embedder (`VOYAGE_API_KEY`) → `accuracy`/cascade (the dominant semantic backend leads, the cascade gates it). `speed` (single-route) stays available but auto-selected by neither.

## Decision

The router is a **profile-driven speed↔accuracy spectrum**, not a single-best-backend dispatcher. A `RouterConfig` (built by `speed_profile()` / `fusion_profile()` / `accuracy_profile()`) names the bundle of strategies; `Router.with_config(backends, config)` attaches it; `RouterConfig()` reproduces the v1 single-route byte-for-byte.

The **shipped default selection rule** lives in `build_store()`:

```python
profile = (os.environ.get("MEMORY_PROFILE") or "").strip().lower()
if not profile:
    profile = "accuracy" if os.environ.get("VOYAGE_API_KEY") else "fusion"
```

and the routing precedence in `Router.route()`:

```python
if self._config.consult2.enabled and self.backends:
    return self._fusion_retriever()        # fan-out + fuse (precedence)
if cascade.enabled and choice == GRAPH and <both backends present>:
    return self._graph_vector_cascade()    # graph→vector cascade
for name in (choice, *self._config.fallback):  # v1 single-route
    ...
```

**`speed` (the true single-route) is never auto-selected.** Default offline = fusion; default with a key = accuracy cascade. `_FusionRetriever` takes precedence over the cascade whenever `consult2.enabled`.

## Rationale

The right default is **capability-dependent, and it was decided by data, not opinion** (the D008 keystone). With only the offline char-n-gram embedder, no backend dominates, so fanning out across all three is the recall-safe choice — fusion is the better default *there*. With a real embedder, the semantic vectors backend dominates and fusing dilutes it (D028), so the cascade (which *gates* the dominant backend rather than averaging it down) is the better default *there*. A profile seam lets the same router be both, and lets the headline `<10%` memory-token-overhead metric be *measured* across the tradeoff curve rather than asserted. Single-route stays the floor for the cheapest possible read, available to anyone who wants it — just not the silent default.

## Tradeoffs & risks

- **The default fans out N× backend searches.** Fusion/cascade issue more *backend* queries than single-route. Mitigated: the efficiency thesis bounds **retrieval context** (returned top-k), not internal search count — `route().search()` still returns one merged top-k, so retrieval-token cost is flat; the cost is CPU/IO of N searches, not context flood. (D023's note: writing/searching all indexes is retrieval-token-neutral.)
- **Fusion can lose to single-route under a dominant backend (D028).** The very default we ship offline would *hurt* if it stayed on once a real embedder is present — which is exactly why the selection rule flips to `accuracy` when `VOYAGE_API_KEY` is set. The fusion-vs-single question at *full benchmark scale* is still **OPEN** (D028 is a small-fixture result); fusion stays a first-class matrix row to be re-measured, not written off.
- **The doc said the opposite.** Until [`architecture.md`](../../architecture.md) is reconciled, a reader believes single-best-backend is live. This ADR is the durable record that it is not; the arch prose (`:9-11`, `:15`, the `:48` "route" node) needs a profile-spectrum rewrite (tracked as a closeout doc-reconciliation item).
- **If `score > RRF` only on small data.** D028 picked score-normalization over RRF on the small fixtures; the method choice is provisional pending the full-scale run.

## Consequences for the build

- **Policy — default selection:** `build_store()` selects `accuracy` with `VOYAGE_API_KEY`, else `fusion`; `MEMORY_PROFILE` overrides. Never `speed` implicitly. Any code reasoning about "the default retrieval shape" must assume fan-out, not single-route.
- **Policy — routing precedence:** `_FusionRetriever` (consult2) > `_GraphVectorCascade` > single-route fallback. A profile cannot enable both meaningfully — fusion wins.
- **Affected files:** [`eval/memeval/router.py`](../../eval/memeval/router.py) (`route()` :961, `_fusion_retriever()` :789, `_FusionRetriever`, `fusion_profile()` :539, `accuracy_profile()`); [`plugin/cookbook_memory/core/contract.py`](../../plugin/cookbook_memory/core/contract.py) (profile selection :72-73, store assembly).
- **Cross-links:** the cascade is [D008]/[D016]; fusion-rejected-as-default is [`ADR`-equivalent D015] (this ADR does NOT reverse it — D015 rejected fusion *as a universal default*; this ships fusion *as the offline-profile default and an opt-in config*, which D015 explicitly allowed). The plugin's auto-profile selection is also recorded in [`ADR-harness-011`](ADR-harness-011-plugin-dumb-client-auto-profile.md) from the harness side; this ADR is the storage-side *why*.
- **Doc-reconciliation owed:** `architecture.md` §module-2 prose + the architecture diagram still assert single-best-backend; reconcile to the profile spectrum + the default-selection rule.
