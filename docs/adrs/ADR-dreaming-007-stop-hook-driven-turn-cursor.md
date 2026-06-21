---
id: ADR-dreaming-007
domain: dreaming
title: Daydream turn definition + Stop-hook-driven cursor model (v1, Claude Code only)
status: Accepted
date: 2026-06-21
contract: false
supersedes: none
superseded_by: none
owner: Scott B. (P4)
origin: design session 2026-06-21 (Daydream PR1 gap pass)
---

# ADR-dreaming-007: Daydream turn definition + Stop-hook-driven cursor model (v1, Claude Code only)

**Status:** Accepted · **Date:** 2026-06-21 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
[`ADR-harness-003`](ADR-harness-003-log-extraction-chunking.md) commits to
"one turn = one chunk + prior-summary overlap," and
[`ADR-harness-004`](ADR-harness-004-dream-state-sidecar.md) commits to a
`cursor` field in the sidecar state as a byte/line offset into the transcript.
Neither ADR defines what *one turn* is in Claude Code's JSONL — that
definition was implicit in the planned harness adapter
([`ADR-harness-005`](ADR-harness-005-log-adapter-redaction.md)), which
[`ADR-dreaming-005`](ADR-dreaming-005-v1-inline-redaction.md) collapsed into
Daydream for v1. Daydream therefore inherits the turn definition.

Daydream is invoked by the plugin's Stop / PreCompact hook per
[`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md), which fires
once per completed Claude response.

## Options considered
- **Stop-hook-driven cursor model: turn = user prompt + Claude's response(s)
  + Stop hook firing; read from sidecar `cursor` to current EOF** (chosen) —
  the Stop hook is the boundary signal *external* to the JSONL; per
  invocation, cursor → EOF *is* one turn by construction. No in-band parsing.
- In-JSONL turn-boundary parser — required if Daydream is ever invoked
  outside the Stop-hook path (replay, backfill). Rejected for v1; adds a
  parser whose record-type assumptions could go stale as CC's JSONL evolves.
- Abstract turn definition only — rejected; the invocation model is the
  mechanism that makes the cursor work without parsing.

## Decision
For v1 (Claude Code only):
- **A turn** = one user prompt + all of Claude's responses (text + tool
  calls + tool results) + the Stop hook firing that marks completion.
- **The boundary signal is the Stop hook**, not a marker inside the JSONL.
  Daydream does not parse turn boundaries from JSONL content.
- **Per-invocation Daydream reads from `sidecar.cursor` to current EOF**;
  that slice is one turn = one chunk
  ([`ADR-harness-003`](ADR-harness-003-log-extraction-chunking.md)).
- **After processing**, Daydream writes `sidecar.cursor = current_eof`.
- **v1 is Stop-hook-only.** No replay path; replay would require an in-band
  parser plus a cursor-reset workflow that v1 does not ship.

## Rationale
The boundary is free: the Stop hook fires after exactly one turn completes,
so cursor → EOF *is* that turn. The chunking invariant from ADR-harness-003
is satisfied by construction without parsing JSONL record types
(`type: user`, `type: assistant`, tool-use records, summary records, slash
commands, sub-agents — none of which Daydream-v1 needs to distinguish).
This keeps the v1 redaction module small and decouples it from CC's evolving
JSONL schema.

## Tradeoffs & risks
- **No replay path in v1.** Re-processing a recorded session requires a
  manual cursor reset (delete sidecar) plus accepting that the entire
  remaining log gets processed as one giant "turn" — useful for forensic
  one-shot replay, wrong for chunk-respecting replay. A real replay path is
  future work.
- **Tied to the invocation model.** If a future plugin change fires Stop
  twice for one turn, Daydream over-chunks (two empty/partial chunks).
  Acceptable failure mode; events stream
  ([`ADR-harness-007`](ADR-harness-007-memory-events-stream.md)) would
  surface it.
- **Sidecar race remains** per
  [`ADR-dreaming-005`](ADR-dreaming-005-v1-inline-redaction.md) open items —
  one Daydream process per session log assumed.
- **Cursor-vs-EOF skew.** If the JSONL is truncated, rotated, or moved
  externally, `sidecar.cursor` becomes invalid. Mitigated by the sanity
  check below.

## Consequences for the build
- **Policy:** Daydream's chunk-extraction reads `sidecar.cursor → EOF` once
  per invocation; treats the slice as exactly one chunk.
- **Policy — cursor sanity check:** before reading, if
  `sidecar.cursor > file_size`, reset to `0` and re-process from the start
  (handles truncation/rotation; emit an event via
  [`ADR-dreaming-009`](ADR-dreaming-009-events-shim.md) for visibility).
- **Policy — no in-band turn parser** ships in v1.
- **Policy — replay deferred.** If a replay path becomes needed, it ships
  with its own in-band parser and its own ADR (turn-boundary parsing across
  CC's record types is the load-bearing part to get right).

## Open items (dreaming-owned)
- **Multi-turn JSONL parser** for the eventual replay path. Future work.
- **Migration to the multi-harness adapter** (when
  [`ADR-harness-005`](ADR-harness-005-log-adapter-redaction.md) lands): the
  adapter will return `list[turn-event]`, at which point Daydream stops
  using the byte-cursor model and consumes turn-events directly. Successor
  ADR will record the migration.
