---
id: ADR-dreaming-001
domain: dreaming
title: Daydreaming — in-session memory capture, auto Stop/PreCompact-fired (day scope)
status: Accepted
date: 2026-06-19
contract: true
supersedes: none
superseded_by: none
owner: Scott B. (P4) — engine; Keith (P1) — hook wiring
origin: docs/harnesses/05-plugin-mvp-plan.md (ADR-P5, split)
---

# ADR-dreaming-001: Daydreaming — in-session memory capture (day scope, auto `Stop`/`PreCompact`-fired)

**Status:** Accepted · **Date:** 2026-06-19 · **Contract:** yes
**Supersedes:** none · **Superseded by:** none

> **Daydreaming and Dreaming are separate functions with totally isolated
> entrypoints.** This ADR covers **Daydreaming** only (in-session capture);
> whole-store consolidation is
> [`ADR-dreaming-002`](ADR-dreaming-002-dreaming-consolidation-cli.md). They may
> share internal helpers (the log adapter, the dedup path, the `LLMClient` of
> [`ADR-dreaming-003`](ADR-dreaming-003-consolidation-llmclient.md)) but they are
> **not** one engine dispatched by a `scope` parameter — they are two distinct
> callables triggered by two distinct surfaces.

## Context
The board's subconscious has two parts. **Daydreaming** is the light, in-session
job: while the model works, *something* should watch the session and capture
memories the model didn't explicitly `remember`. This is distinct from **memory
creation in-loop** — the model writes via the `remember` MCP tool
([`ADR-harness-002`](ADR-harness-002-recall-remember-mcp-tools.md)); Daydreaming
additionally mines the transcript for what the model *didn't* save.

An earlier draft conflated Daydreaming with whole-store consolidation under one
`dream(scope=…)` engine. That framing is rejected here: the two are separate
functions with isolated entrypoints (see the note above).

## Options considered
- **Daydreaming fires automatically on the `Stop`/`PreCompact` hook** (chosen) — day
  scope, current session only, running as the session proceeds with no manual step.
- A CLI-only / manual trigger for in-session capture — rejected: loses the automatic
  "remember as you work" the Daydreamer exists to provide; it is a first-class MVP
  component, not a manual step.
- A long-lived background daemon watching the log — rejected: an unmanaged daemon
  lifecycle, which the Codex floor (no session-end signal) cannot clean up.

## Decision
**Daydreaming is its own function, fired automatically by the plugin's `Stop` hook
(`async: true`, with `PreCompact` as a final pre-compaction pass).** It runs over the
new-since-cursor session log: adapter → chunk → cheap model → write through the
Orchestrator → advance the cursor (see
[`ADR-harness-003`](ADR-harness-003-log-extraction-chunking.md) /
[`ADR-harness-004`](ADR-harness-004-dream-state-sidecar.md)). It operates on **this
session only** and has **no** CLI consolidation responsibilities.

## Rationale
Firing on `Stop`/`PreCompact` gives automatic in-session memory capture — the
"subconscious watching the session" the board calls for — without an unmanaged
daemon (the hook is the harness's own lifecycle event, self-backgrounded; the
Codex-floor call). Keeping it a distinct entrypoint from whole-store consolidation
means each can be reasoned about, triggered, tested, and turned on/off
independently, which the three-iteration ramp needs (iter-2 = Daydreaming +
`remember` on, night consolidation off).

## Tradeoffs & risks
The `Stop`-fired Daydreamer runs inside every `claude -p` eval run, so the eval is
**not** purely "drive + dream between batches" — in-session day-dreaming happens
automatically during each run. That's intended (it's how a real user's session
behaves), and it stays black-box because the trigger is the harness's own hook, not
an eval call. A memory the Daydreamer writes mid-session reaches context via the next
`recall` (instantly searchable) or the next-prompt `UserPromptSubmit` push; there is
**no** force-injection before the next model call (no such CC hook exists).

## Consequences for the build

- **Contract — source of truth:** the Daydreaming entrypoint + the plugin
  `Stop`/`PreCompact` hooks.
- **Shape:** an isolated entrypoint, e.g. `daydream(*, session_id, log_path, store)`
  (separate from the consolidation entrypoint in
  [`ADR-dreaming-002`](ADR-dreaming-002-dreaming-consolidation-cli.md)) — **not** a
  `dream(scope=…)` dispatch. The plugin invokes it via `memory daydream --session
  <id> --log <transcript_path>` (async). Accepts `--store P`.
- **Policy — hooks wired:** `Stop` (`async: true`) and `PreCompact` → the Daydreaming
  pass.
- **Exhaustive consumers:** the plugin `Stop`/`PreCompact` hooks (the trigger), and
  the Daydreaming engine itself.
- **Policy:** writes go **through the Orchestrator**
  ([`ADR-storage-001`](ADR-storage-001-orchestrator-in-process-library.md)), never
  the store directly.

## Open items (dreaming-owned)
- The internal Daydreaming engine (chunk → extract → write) and any helpers it shares
  with consolidation are Scott's; the **entrypoints stay isolated** regardless of
  shared internals.
