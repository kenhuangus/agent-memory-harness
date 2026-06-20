---
id: ADR-harness-007
domain: harness
title: Structured memory-events stream, Langfuse-bound
status: Accepted
date: 2026-06-19
contract: true
supersedes: none
superseded_by: none
owner: Keith (P1)
origin: docs/harnesses/05-plugin-mvp-plan.md (ADR-P11)
---

# ADR-harness-007: Structured memory-events stream, Langfuse-bound

**Status:** Accepted · **Date:** 2026-06-19 · **Contract:** yes
**Supersedes:** none · **Superseded by:** none

## Context
The system needs to surface "what got remembered / recalled / dreamed" — both for
debugging and so the **black-box eval can verify behavior from an output it reads,
without touching internals** ([`ADR-eval-001`](ADR-eval-001-extract-memory-package.md)).

## Options considered
- **A structured memory-events stream** (chosen): JSONL under `$MEMORY_STORE` for
  MVP, shaped to be observability-platform-friendly (Langfuse) later.
- Reuse the eval engine's `Trajectory` JSONL — rejected: it re-couples the plugin to
  eval internals (fights [`ADR-eval-001`](ADR-eval-001-extract-memory-package.md)).

## Decision
The plugin, `memory dream`, and the Orchestrator emit a **structured memory-events
stream** (JSONL under `$MEMORY_STORE` for MVP), shaped to be **observability-
platform-friendly so it can be shipped to Langfuse** (or similar) later as
spans/traces.

## Rationale
Cheap, and it doubles as the exact machine-readable output the eval black box reads
to confirm "what got remembered." Designing the event shape as trace-friendly now
(operation, ids, timing, parent/child) means the Langfuse export is a sink swap,
not a re-instrumentation.

## Tradeoffs & risks
A second log alongside the engine's `Trajectory` JSONL — but reusing
`memeval.trajectory` would re-couple the plugin to eval internals (fights
[`ADR-eval-001`](ADR-eval-001-extract-memory-package.md)), so a separate,
plugin-owned events stream is correct.

## Consequences for the build

- **Contract — source of truth:** the memory-event schema in the memory package.
- **Shape:** `{ts, op: "recall"|"remember"|"dream"|"error", scope?:
  "session"|"all", session_id, ids:[...], query?, summary?, meta:{...}}` —
  span-friendly.
- **Exhaustive consumers:** the MCP tools, the Daydreamer (`Stop`/`PreCompact`), and
  `memory dream --all` (emitters); the `memory log`/`stats` CLI and the eval
  verification step (readers); a future Langfuse exporter (sink).

## Open items (Keith, later)
- **Langfuse export:** wire the events stream to a real observability platform.
