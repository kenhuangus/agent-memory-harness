---
id: ADR-harness-006
domain: harness
title: Everything fail-open — never break the user's session
status: Accepted
date: 2026-06-19
contract: false
supersedes: none
superseded_by: none
owner: Keith (P1)
origin: docs/harnesses/05-plugin-mvp-plan.md (ADR-P10)
---

# ADR-harness-006: Everything fail-open — never break the user's session

**Status:** Accepted · **Date:** 2026-06-19 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
The Orchestrator's parts (`router`, `stores`, `dreaming`) are `NotImplementedError`
scaffolds today and depend on Brent's/Scott's work landing. The plugin runs inside a
live coding session.

## Options considered
- **Every hook and MCP tool is fail-open** (chosen) — degrade to a safe default on
  any error.
- Fail-closed / surface errors to the session — rejected: a memory error would crash
  or block the user's turn, which is strictly worse than no memory.

## Decision
**Every hook and MCP tool is fail-open.** If the Orchestrator/store/model errors or
isn't ready: `recall` returns empty, `remember` no-ops (logs a warning), the `Stop`
Daydreamer pass swallows and logs. A memory failure must never crash or block the
user's turn.

## Rationale
A memory system that breaks the user's session is strictly worse than no memory.
Fail-open lets the plugin ship and be used while the engine matures, and is what
makes the three-iteration ramp safe to run before each layer lands: **(1)** memory +
dreaming both no-op → baseline; **(2)** `recall`/`remember` + Daydreamer wired,
night `dream` still no-op; **(3)** night consolidation live.

## Tradeoffs & risks
Silent degradation can mask real breakage — mitigated by the events stream
([`ADR-harness-007`](ADR-harness-007-memory-events-stream.md)), which records "recall
failed / store unavailable" so failures are *visible* even though they're non-fatal.

## Consequences for the build

- **Policy:** hook scripts and tool handlers wrap all engine calls; errors → log +
  safe default.

## Open items (team-owned)
- **Build-vs-wait sequencing:** whether the plugin is built/tested against
  `InMemoryStore` first (de-risks the dependency, some throwaway wiring) or waits for
  the real backends (no throwaway, but blocks plugin work). The roadmap assumes
  "build against `InMemoryStore` first," but the call is the team's.
