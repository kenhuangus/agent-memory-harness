---
id: ADR-storage-004
domain: storage
title: The router owns the write path (where/how to store); base_all is the recall-safe default; RouterStore is the live store facade
status: Accepted
date: 2026-06-23
contract: false
supersedes: none
superseded_by: none
owner: Brent (P3)
origin: DECISION_LOG D009/D023/D025 (capstone-workspace); live-adoption ADR-harness-011
---

# ADR-storage-004: The router owns the write path; `base_all` is the recall-safe default; `RouterStore` is the live store facade

**Status:** Accepted · **Date:** 2026-06-23 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context

[D009] established the ownership rule: the **primary agent decides IF** something is stored; the **router decides WHERE & HOW**. But only the *retrieval* half was built — a sanity audit confirmed the agent wrote every memory to a single hardcoded store, so "the router owns where/how" was true for reads and a lie for writes. Brent's directive (D023): *"not done until the router is as accurate as we can make it on both writes and retrievals."*

Two gaps had to close:

1. **The router had no write-routing.** [`router.py`](../../eval/memeval/router.py) gained `route_write(item) -> list[MemoryStore]` (D023, PR #56) — the write-path mirror of `route()`, returning the backend(s) to persist an item into per a `write_policy`. Markdown is the always-written literal base (D001).
2. **Write-routing was built but DEAD (D025).** A blind cross-repo impact scan confirmed `route_write`/`Router.write` had *zero callers* outside `router.py`+tests: the plugin `_Engine.remember` hardcoded a markdown write, and the framework's write methods were `NotImplementedError` stubs. The blocker to making it live solo: **`Router` is not a `MemoryStore`** (`write` returns a `WriteReceipt`, `route` returns a *backend*, no `get`/`all`), so it couldn't be dropped into any seam that expects a store.

## Options considered

- **Selective placement** (`base_selective`/`single` — store each memory only where its content classifies). The intuitive default. **Rejected as default by measurement (D023):** over 24 blind (memory, query) round-trip pairs, `single` and `base_selective` both scored **0.708** round-trip recall vs `base_all` **1.000** — because a memory's *content* and its later matching *query* classify to **different** backends under the rule classifier, so placing a memory only where its content lands misses the query that routes elsewhere.
- **Write every index (`base_all`):** markdown + vectors + graph for every memory. Costs more index-writes (real-embedder API calls, graph-node bloat) but is the only policy that makes a memory retrievable wherever its query lands.
- **For the live seam:** wait on Keith to wire the two stub sites (cross-team, slow); OR build a `MemoryStore` adapter over `Router` solo (chosen via AskUserQuestion) — the exact glue Keith needs anyway, attacking the headline-metrics gap without blocking.

## Decision

**The router owns the write path.** `Router.route_write(item)` returns the backend store(s) per `RouterConfig.write_policy`; the recall-safe **default `write_policy = base_all`** (markdown + vectors + graph for every memory). `base_selective`/`single` remain config options (they save index-writes but cost ~30% round-trip recall; they only pay off with an *aligned* learned/semantic classifier whose content- and query-classification agree — the D007 north-star).

**`RouterStore`** ([`router.py`](../../eval/memeval/router.py):1109) is a thin `MemoryStore` facade over `Router`: `write` → `Router.write` (dedup → `route_write` → fan to every policy backend); `search` → `route(query).search(...)` (k/as_of/kwargs preserved); `get`/`all` union + de-dup across backends (markdown base scanned first so its revision wins ties); `delete` → `Router.delete > 0`. It is what [`contract.build_store()`](../../plugin/cookbook_memory/core/contract.py) **returns** — `RouterStore(Router.with_config(backends, config))` — replacing the plugin's old hardcoded-markdown write (adopted #76/#79, [`ADR-harness-011`](ADR-harness-011-plugin-dumb-client-auto-profile.md)).

## Rationale

Write-routing *sounds* like it should be selective; the **round-trip measurement before committing a default** (same discipline as D021/D022/D024) showed selective silently loses ~30% recall, so the recall-safe default is to write every index. Writing all indexes is **retrieval-token-neutral** — the efficiency thesis bounds *retrieval* context (`route()` still returns one backend's top-k), so the only cost of `base_all` is *indexing*, not retrieval tokens. `RouterStore` is the minimal adapter that makes the built-but-dead write path *live* without waiting on a cross-team integration: it re-shapes the Router into the five-method protocol so every store-shaped seam (plugin `_Engine`, framework, the native eval `store=`) drives routed writes unchanged. Purely additive — the Router contract is untouched.

## Tradeoffs & risks

- **`base_all` triples index-writes per memory.** Real-embedder API calls and graph-node growth scale with writes. Accepted: indexing cost, not retrieval cost; selective only wins once an *aligned* learned classifier ships, at which point the policy flips by config.
- **`get`/`all` must collapse fan-out copies.** Because `base_all` writes the same item to several backends, `RouterStore.get`/`all` de-dup by `item_id` in backend-priority order (markdown first). A bug here would surface duplicates; covered by the adapter eval (D025: cross-backend read-dedup).
- **Router-is-not-a-store is a real asymmetry to maintain.** `Router.write -> WriteReceipt` vs `RouterStore.write -> None` (receipt parked on `last_receipt`); `Router.delete -> int` vs `RouterStore.delete -> bool`. Every facade method must honor the protocol return type while the Router keeps its richer one. Documented inline; locked by the adapter conformance tests.
- **The architecture doc lags.** §3 lists only store-level `write(item)`; §4 still says the framework is merely "backed by Brent's stores/ + router.py" with no `RouterStore`/`build_store` seam. Reader could miss that the live write layer *is* a RouterStore. Doc-reconciliation owed.

## Consequences for the build

- **Policy:** the router decides WHERE to store, not just where to read. The agent's only write input is "remember, yes/no" (D009). Default `write_policy = base_all`; selective policies are opt-in config.
- **Policy:** `build_store()` returns a `RouterStore`; **no live path may hardcode a single-backend write** — the markdown hardcode is gone.
- **Affected files:** [`eval/memeval/router.py`](../../eval/memeval/router.py) (`route_write` :999, `_write_backend_names`/`base_all` :1025, `Router.write`, `RouterStore` :1109); [`plugin/cookbook_memory/core/contract.py`](../../plugin/cookbook_memory/core/contract.py):102 (`return RouterStore(...)`).
- **Cross-links:** dedup-on-write is [`ADR-storage-005`](ADR-storage-005-dedup-on-write-default-off.md); the profile spectrum that `route()` reads is [`ADR-storage-003`](ADR-storage-003-router-profile-spectrum-fusion-default.md); the plugin-side adoption is [`ADR-harness-011`](ADR-harness-011-plugin-dumb-client-auto-profile.md).
- **Doc-reconciliation owed:** `architecture.md` §3/§4 — add `route_write`/`write_policy`/`base_all` and the `RouterStore`/`build_store` live seam.
