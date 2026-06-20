---
id: ADR-eval-001
domain: eval
title: Extract the memory system into its own package
status: Accepted
date: 2026-06-19
contract: false
supersedes: none
superseded_by: none
owner: Ken (P2) — boundary; team sign-off required to execute
origin: docs/harnesses/05-plugin-mvp-plan.md (ADR-P1)
---

# ADR-eval-001: Extract the memory system into its own package

**Status:** Accepted · **Date:** 2026-06-19 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
Today the Orchestrator pieces (`MemoryFramework`, `router.py`, `stores/`,
`dreaming/`) live *inside* `eval/memeval/`, and `architecture.md` hands the
framework to `agent.run_agent(store=…)` — i.e. the eval harness holds the memory
system directly. The binding principle established in the 2026-06-18 design
session is the opposite: **the eval engine must have zero knowledge of the memory
system's internals** and must interact with it only by driving the coding harness
(`claude -p`) with the plugin installed. This decision is about the eval↔memory
boundary, which is why it is filed under the eval domain even though Keith owns the
mechanics of the move.

## Options considered
- **Extract to its own top-level package** (e.g. `cookbook_memory/`): Orchestrator
  + stores + router + dreaming + Daydreamer + the CC plugin live there; `memeval`
  keeps only eval/benchmark code.
- **Leave in `memeval`, enforce the boundary by discipline** (CODEOWNERS +
  convention): the coupling stays physically possible.
- **Document the target split, defer the move:** plan against the boundary now,
  physically extract later.

## Decision
**Extract the memory system into its own top-level package.** `memeval` becomes a
pure black-box driver.

## Rationale
The black-box boundary is only *real* if the eval engine *cannot* import the
memory internals. A shared package makes the wrong thing easy and the boundary a
matter of willpower; separate packages make the black-box principle structural.
It also makes the memory system the genuine, distributable, harness-agnostic
artifact the project is aiming for, rather than a subfeature of an eval harness.

## Tradeoffs & risks
This is a **refactor of Brent's and Scott's current paths** (`stores/`, `router.py`,
`dreaming/`) and needs team buy-in — it is not Keith's to execute unilaterally.
The frozen `schema.py`/`protocols.py` (the shared contract) must be reachable by
both packages, so they either stay shared or are published as a tiny contract
package both import. Until the move happens, the plugin can be built against the
current paths (the boundary is honored by discipline in the interim).

## Consequences for the build
- **Policy:** the plugin and Daydreamer import from the **memory-system package**,
  never from `memeval`.
- **Policy:** the only eval↔memory seam is `$MEMORY_STORE` (a path) plus the
  plugin's externally-observable outputs (the events stream — see
  [`ADR-harness-007`](ADR-harness-007-memory-events-stream.md)).

## Open items (team-owned)
- **When and how** to physically move `stores/` / `router.py` / `dreaming/` /
  `MemoryFramework` out of `eval/memeval/`, and how the frozen
  `schema.py`/`protocols.py` contract is shared between the two packages. Needs
  Brent + Scott + Ken sign-off (CODEOWNERS).
- Reconcile `architecture.md` §1/§2/§4 (which still describe the eval harness
  *holding* the Orchestrator) via a `[CONTRACT]`-style PR with all owners.
