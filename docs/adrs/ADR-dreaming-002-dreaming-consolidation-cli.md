---
id: ADR-dreaming-002
domain: dreaming
title: Dreaming — whole-store consolidation via memory dream --all CLI (night scope)
status: Accepted
date: 2026-06-19
contract: true
supersedes: none
superseded_by: none
owner: Scott B. (P4)
origin: docs/harnesses/05-plugin-mvp-plan.md (ADR-P5, split)
---

# ADR-dreaming-002: Dreaming — whole-store consolidation via `memory dream --all` CLI (night scope)

**Status:** Accepted · **Date:** 2026-06-19 · **Contract:** yes
**Supersedes:** none · **Superseded by:** none

> **Daydreaming and Dreaming are separate functions with totally isolated
> entrypoints.** This ADR covers **Dreaming** (whole-store consolidation) only;
> in-session capture is
> [`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md). They may share
> internal helpers (dedup, the `LLMClient` of
> [`ADR-dreaming-003`](ADR-dreaming-003-consolidation-llmclient.md)) but they are
> **not** one engine dispatched by a `scope` parameter — they are two distinct
> callables triggered by two distinct surfaces.

## Context
The deep half of the board's subconscious is **consolidation**: dedup /
conflict-resolution / retention over the **whole store across all sessions**. This is
a different job from in-session capture
([`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md)) — different scope
(entire memory vs. one session), different trigger (deliberate vs. automatic),
different state (reads memory hashes/timestamps vs. a per-session log cursor).

The eval protocol drives the cycle **run 5 → consolidate → run 5 → … → measure**, so
this consolidation pass must be invokable between batches via a **public** surface
that doesn't reach into internals.

## Options considered
- **A public `memory dream --all` CLI** (chosen) — the eval drives it between batches
  and a human can run it; consolidation is a deliberate, whole-store pass.
- Auto-firing consolidation on a session hook — rejected: whole-store consolidation
  isn't a per-session event; coupling it to `Stop` would re-merge it with Daydreaming
  and run expensive cross-session work on every turn.
- An internal function the eval imports and calls — rejected: it would breach the
  black-box boundary ([`ADR-eval-001`](ADR-eval-001-extract-memory-package.md)).

## Decision
**Dreaming is its own function with its own entrypoint: the public `memory dream
--all` CLI.** It consolidates the **whole store across all sessions** (dedup /
conflict / retention). The eval invokes it between batches (black-box-safe — a public
action, not an internal seam) and a human can run it. It has **no** per-session log
responsibilities.

## Rationale
Keeping consolidation on a public CLI keeps the eval's run→consolidate→measure cycle
a clean black box and is exactly the cross-session pass that benefits from being run
deliberately between batches. Making it a distinct entrypoint from Daydreaming
(rather than `dream(scope="all")`) means the two functions are triggered, tested, and
ramped independently — the no-op ramp holds: iter-2 ("memory on, consolidation off")
runs Daydreaming + `remember` while this CLI is still a no-op; iter-3 turns it on.

## Tradeoffs & risks
A second subconscious surface to build and document, but it is a genuinely different
job (whole-store consolidation vs. per-session capture), so the split is honest, not
incidental. Running it between batches means consolidation latency is on the eval's
critical path between batches — acceptable and deliberate (it is "night," run when the
agent is otherwise idle).

## Consequences for the build

- **Contract — source of truth:** the `memory dream --all` CLI + the consolidation
  entrypoint behind it.
- **Shape:** an isolated entrypoint, e.g. `consolidate(store, *, …)` (separate from
  the Daydreaming entrypoint in
  [`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md)) — **not** a
  `dream(scope=…)` dispatch. Surfaced as `memory dream --all` (accepts `--store P`).
- **Exhaustive consumers:** the eval protocol + a human (the trigger), and the
  consolidation engine itself.
- **Policy:** writes/merges go **through the Orchestrator**
  ([`ADR-storage-001`](ADR-storage-001-orchestrator-in-process-library.md)), never
  the store directly.

## Open items (dreaming-owned)
- **Consolidation logic** — dedup (exact / semantic / near-dup), conflict detection +
  reconciliation rules (recency, confidence, source), retention & pruning — is
  Scott's. It reads memory hashes/timestamps from the store to find what changed; it
  does **not** use the per-session cursor of
  [`ADR-harness-004`](ADR-harness-004-dream-state-sidecar.md).

  **CLOSED 2026-06-23** (per execution; closed by the Job 3 PR shipping the
  governance pass in `eval/memeval/dreaming/worker.py` against
  `JOB3_GOVERNANCE_RUBRIC.md`). All four ADR-002 jobs have now landed:

  - **Job 1 (dedup)** — PR #88 (detection) + PR #98 (mutation). Worker
    deletes cluster losers via `self.store.delete`, picks winner by
    recency-latest + lex-tiebreak.
  - **Job 4 (TTL pruning)** — PR #103. Worker drops items past
    `DREAM_ITEM_RETENTION_DAYS` (default 30; `=0` disables) via
    `self.store.delete`; runs BEFORE dedup.
  - **Job 2 (contradiction resolution)** — PR #105. LLM-driven pair
    detection; deterministic worker-side loser-selection by the Job 1
    recency rule; loser retired via `self.store.delete`.
  - **Job 3 (governance: must-know / must-do / blacklist)** — closes
    THIS PR. LLM-driven per-item classification; blacklist via
    `self.store.delete` (same primitive); must-know and must-do are
    **SOFT** advisories surfaced in `summary.governance` block (no
    mutation, no recall-side enforcement in v1 — pinned as
    forensic-only by halliday B4).

  All four jobs share the same basedir flock (ADR-021 Decision 2), the
  same NFS hard-fail (Decision 3), the same Daydream serialization
  (Decision 4), and the same `self.store.delete` mutation primitive
  (ADR-021 §Policy). No successor ADR was required; ADR-021's mutation
  envelope held end-to-end.
