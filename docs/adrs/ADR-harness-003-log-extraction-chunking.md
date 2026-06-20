---
id: ADR-harness-003
domain: harness
title: Log-extraction chunking — one turn = one chunk, with prior-summary overlap
status: Accepted
date: 2026-06-19
contract: false
supersedes: none
superseded_by: none
owner: Keith (P1)
origin: docs/harnesses/05-plugin-mvp-plan.md (ADR-P7)
---

# ADR-harness-003: Log-extraction chunking — one turn = one chunk, with prior-summary overlap

**Status:** Accepted · **Date:** 2026-06-19 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
When `memory dream` extracts memories from the session logs
([`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md)), it must chunk
the new-since-cursor log slice before the model call. The meeting preferred
"semantic grouping over arbitrary line counts/time windows, with overlap," but
flagged the exact heuristic as open. The on-disk cursor
([`ADR-harness-004`](ADR-harness-004-dream-state-sidecar.md)) gives a natural "new
since last dream" boundary; within that, the turn is the natural unit.

## Options considered
- **One turn = one chunk + prior-summary overlap:** send the new turn-slice
  (prompt + assistant + tool calls/results since cursor) as one chunk; overlap =
  carry the prior turn's one-line summary as a header.
- **Semantic segmentation within the slice** (topic-shift detection) — truest to
  the meeting, but the heuristic is itself the open problem.
- **Size-bounded sliding window + overlap** — the "arbitrary windows" the meeting
  said to avoid.

## Decision
**One turn = one chunk, with the prior turn's summary as overlap.** The Adapter is
designed so a semantic segmenter slots in later.

## Rationale
A turn is already a semantically meaningful unit of work. This gets working,
defensible log-extraction for Monday without solving the open segmentation problem,
and the Adapter's neutral event sequence is exactly the seam where a smarter
segmenter drops in once eval data shows turn-chunking is too coarse
(thinnest-slice-first; extend one axis later).

## Tradeoffs & risks
A long multi-tool turn is one large chunk — acceptable for MVP; the cheap model can
handle it, and the events stream
([`ADR-harness-007`](ADR-harness-007-memory-events-stream.md)) lets us *see* when
chunks get unwieldy. Not "semantic grouping" in the sophisticated sense — that's the
planned next axis, not abandoned.

## Consequences for the build

- **Policy:** the **Adapter** (`adapters/claude-code/log_adapter`) normalizes CC
  JSONL into a neutral `list[turn-event]`; chunking operates on that neutral shape,
  so OpenCode's SQLite log / Codex's format plug in behind the same Adapter
  interface later ("start with JSONL only").

## Open items (Keith, later)
- **Semantic chunker:** upgrade from one-turn-one-chunk to topic-shift segmentation,
  driven by eval data.
