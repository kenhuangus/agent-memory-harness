---
id: ADR-harness-004
domain: harness
title: dream state — on-disk JSON sidecar (cursor + last_summary + recent_memory_ids)
status: Accepted
date: 2026-06-19
contract: false
supersedes: none
superseded_by: none
owner: Keith (P1)
origin: docs/harnesses/05-plugin-mvp-plan.md (ADR-P8)
---

# ADR-harness-004: `dream` state — on-disk JSON sidecar (cursor + last_summary + recent_memory_ids)

**Status:** Accepted · **Date:** 2026-06-19 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
`memory dream` (day-scope) is invoked repeatedly across a session's lifetime and
must not re-extract log it already processed, so it needs state that survives
between invocations on disk. The meeting named two pieces: the cursor (last
processed log line) and a "recently written memory cache."

## Options considered
- **A small JSON sidecar keyed by session id** (chosen): `{cursor, last_summary,
  recent_memory_ids}` under the store dir.
- A table inside the store DB — couples per-session day-dream state to the store
  schema (Brent's) for no real gain; the sidecar keeps it plugin-local.

## Decision
A small **JSON sidecar keyed by session id**, under the store dir:
`{cursor, last_summary, recent_memory_ids}`.

## Rationale
Lets each day-scope `memory dream --session <id>` resume where the last left off.
`cursor` = byte/line offset into the transcript (resume point — only newly-appended
log is extracted). `last_summary` = the prior-chunk summary used as overlap
([`ADR-harness-003`](ADR-harness-003-log-extraction-chunking.md)).
`recent_memory_ids` = the meeting's "recently written cache" — tells the next
`dream` what was already written so it doesn't re-extract, and can hint the
Orchestrator's dedup. Per-session keying keeps concurrent sessions independent.
Night-scope (`--all`) consolidation reads memory hashes/timestamps from the store
itself, so it doesn't use the per-session cursor.

## Tradeoffs & risks
`recent_memory_ids` is per-client, not a global cache (consequence of
[`ADR-storage-001`](ADR-storage-001-orchestrator-in-process-library.md)'s library
model) — bounded in size (last N), with the Orchestrator's dedup-on-write as the
real backstop.

## Consequences for the build

- **Policy — sidecar path:** `${MEMORY_STORE%/*}/dream/<session_id>.json` (or a
  sibling dir). Read at the start of each day-scope `dream`, written at the end.
