---
id: ADR-harness-011
domain: harness
title: The plugin is a dumb client of the router; the engine selects the routing profile
status: Accepted
date: 2026-06-22
contract: false
supersedes: none
superseded_by: none
owner: Keith (P1)
origin: design session 2026-06-22
---

# ADR-harness-011: The plugin is a dumb client of the router; the engine selects the routing profile

**Status:** Accepted · **Date:** 2026-06-22 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
The plugin reaches the storage seam through one import edge (`plugin/cookbook_memory/core/contract.py`, per [`ADR-eval-001`](ADR-eval-001-extract-memory-package.md)). Until now that seam (`load_engine()`) handed back the **raw** engine classes — `Router` and the three concrete stores — and the plugin's `_Engine` (`core/client.py`) did the assembly itself: it instantiated `SqliteVectorStore`/`MarkdownStore`/`GraphStore` with hard-coded constructor args and called the bare `Router(backends)`.

That assembly was wrong in two load-bearing ways:

1. **It pinned recall to the lowest-capability profile.** `Router(backends)` is byte-for-byte the v1 *speed* profile (`RouterConfig()`): rule classifier, offline hashing embedder, cascade OFF, fusion OFF. The router has since grown a whole accuracy spectrum — `Router.with_config()` plus `speed_profile()`/`accuracy_profile()`/`fusion_profile()`, the graph→vector cascade, cross-backend RRF/score fusion, a semantic-exemplar classifier, real embeddings, the reranker, and dedup + write-routing. **None of it was reachable** from the plugin, because the seam never exported the config surface and the plugin never called `with_config`.

2. **It bypassed the router's write path.** `remember` wrote *directly* to the markdown backend, so dedup + multi-index write-routing (which the router owns) were dead code, and the plugin — not the router — was deciding *where* a memory landed. The router is the component that owns *where to store* ([`ADR-storage-001`](ADR-storage-001-orchestrator-in-process-library.md)); the plugin had quietly taken that decision.

The frozen storage contract already provides the right shapes to fix this — the question was *who assembles them and who chooses the profile*.

## Options considered
- **Plugin selects/configures the profile** — extend the seam to export `RouterConfig` + the profile factories and let `client.py` build the config from env. *Rejected:* it pushes routing internals (classifiers, embedders, cascade/fusion knobs, the embedder↔vector-store dimension constraint) into the plugin, which is exactly the leak we're removing. The plugin would have to know that profiles exist and which one is safe offline.
- **Keep the bare default, add nothing** — *rejected:* leaves every accuracy feature dead and the markdown-only write in place. This is the status quo the change exists to kill.
- **Seam owns all assembly; plugin is a dumb client; engine auto-selects the profile** (chosen) — the seam grows one factory that builds the backends, picks the profile, wires the matching classifier/embedder/cascade/fusion, and returns one opaque `MemoryStore`. The plugin holds that object and calls `search`/`write` on it, knowing nothing of profiles, backends, or embedders.

Within the chosen option, a secondary choice — **what default profile** when nothing is configured:
- *speed* — the old behavior; lowest recall, never re-selected once we have better offline options.
- *fusion* (chosen for the no-key default) — cross-backend RRF; the best **fully offline** recall, needs no API key, and uses the store's default embedder so there is no embedder↔vector-store dimension mismatch.
- *accuracy* (chosen when a real-embedder key is present) — semantic classifier + Voyage embedder + cascade; best recall, but requires `VOYAGE_API_KEY` and a vector store built at the embedder's dimension, so it cannot be the keyless default.

## Decision
The contract seam owns **all** engine assembly behind a single factory, `build_store(store_path)`, which returns one opaque `MemoryStore` — a `RouterStore` over `Router.with_config(backends, config)`. The plugin's `_Engine` is a dumb client: `recall` → `store.search`, `remember` → `store.write`. It specifies no profile, backend, embedder, or classifier.

The **engine** selects the routing profile, with zero plugin input:

- `$MEMORY_PROFILE` (`speed` | `fusion` | `accuracy`) forces a profile when set.
- Otherwise: `VOYAGE_API_KEY` present → **accuracy**; else → **fusion**. *speed* is never auto-selected (reachable only by explicit `$MEMORY_PROFILE=speed`).

The accuracy branch is the only one that swaps in a real embedder, so it — and only it — builds the vector store around that embedder (matching dimension); every other profile uses the store's offline default. `RouterStore` is the deliberate wrapper: the bare `Router` is not a `MemoryStore` (its `write` returns a `WriteReceipt`, `route` returns a backend, it has no `get`/`all`), so without the facade the dedup + write-routing path stays dead code. Wrapping it makes routed read **and** routed write live through the one object.

## Rationale
The plugin's job is to be a conscious-surface client of memory, not a router builder. Putting all assembly behind the seam means the plugin can never again pin a stale profile or make a storage-placement decision, and the router can grow new strategies (a new profile, a learned classifier, a different fusion method) without touching the plugin at all. Auto-selecting the *best profile that works in the current environment* — fusion offline, accuracy when a key is present — gives the strongest recall available with no configuration, while `$MEMORY_PROFILE` keeps an explicit escape hatch for tests and benchmarking. This is the one-sentence defense: **the plugin consumes the router at full capability and specifies none of its internals.**

## Tradeoffs & risks
- **Default behavior changed** from speed to fusion/accuracy. Recall results differ from the prior bundle; mitigated by the full plugin suite passing (38/38) and all four profile paths verified to construct and round-trip. `$MEMORY_PROFILE=speed` restores the old behavior exactly.
- **Hidden env-driven branching.** A `VOYAGE_API_KEY` in the environment silently changes the profile (and rebuilds the vector store at 1024-dim). This is intended (use the better path when the key is there) but means the active profile is not obvious from the plugin code alone — it is documented in `build_store`'s docstring and selectable explicitly via `$MEMORY_PROFILE`.
- **Accuracy path is only construction-verified offline** (mocked embedder); the live Voyage path (key + dimension rebuild + network) is untested against the real API. Revisit before relying on accuracy in production.
- **`WriteReceipt` is discarded** by `remember` (merge/fan-out info is available on `RouterStore.last_receipt` but unused). Acceptable now; a follow-up could surface it in the debug CLI.
- **Fail-open still swallows assembly errors.** If `build_store` raises (e.g. a future profile misconfiguration), the client degrades to a no-op recall/remember per [`ADR-harness-006`](ADR-harness-006-fail-open.md) — correct for never breaking a turn, but it means a broken profile manifests as empty recall rather than a loud failure. The `$MEMORY_PROFILE` escape hatch is the manual recovery.

## Consequences for the build
- **Policy:** the plugin MUST reach the engine only through `build_store()` and treat the result as an opaque `MemoryStore`. No plugin module may import a concrete store, `Router`, `RouterConfig`, an embedder, or a classifier, or branch on profile.
- **Policy:** *where* a memory is stored is the router's decision (`RouterStore.write` → `Router.write` → dedup + write-routing). The plugin MUST NOT write to a named backend directly.
- **Policy:** profile selection lives in `build_store`. A new profile, default change, or embedder swap is made there (or via `$MEMORY_PROFILE`), never in the plugin client.
- **Not a contract:** this records how the harness *consumes* the storage seam ([`ADR-storage-001`](ADR-storage-001-orchestrator-in-process-library.md)); it establishes no new cross-workstream shape, so `contract: false`. It builds against the router's existing public surface (`Router.with_config`, `RouterStore`, the profile factories) without changing it.
