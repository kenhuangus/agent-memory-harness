---
id: ADR-harness-005
domain: harness
title: The log adapter owns redaction before any model call
status: Accepted
date: 2026-06-19
contract: false
supersedes: none
superseded_by: none
owner: Keith (P1)
origin: docs/harnesses/05-plugin-mvp-plan.md (ADR-P9)
---

# ADR-harness-005: The log adapter owns redaction before any model call

**Status:** Accepted · **Date:** 2026-06-19 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
The Daydreamer reads the full session transcript — which can contain secrets (API
keys, tokens, `.env` values, file contents) — and sends chunks to an external model
(OpenRouter, [`ADR-dreaming-003`](ADR-dreaming-003-consolidation-llmclient.md)). The
trust boundary is: untrusted/sensitive log content crossing to a third party.

## Options considered
- **The log adapter redacts before any chunk leaves the process** (chosen) — scrub
  obvious secret patterns from the turn-slice prior to the model call.
- Redact in the model client — too late and too general; the adapter is the single
  point where session content is assembled for the model, so redaction belongs there.
- No redaction, rely on the user picking a local model — leaves the default
  (OpenRouter) path leaking; unacceptable.

## Decision
**The log adapter performs a redaction pass before any chunk leaves the process** —
scrub obvious secret patterns (key/token shapes, `.env`-style assignments) from the
turn-slice prior to the model call. This is *our* boundary (the Daydreamer is ours).

## Rationale
The plugin controls the only point where session content leaves the machine (the
model call), so redaction belongs there, in the adapter, before chunking. It is a
documented, bounded boundary rather than a hand-wave.

## Tradeoffs & risks
Pattern-based redaction is best-effort, not exhaustive — it reduces, not eliminates,
leakage risk. Users wanting zero external exposure can select the local `LLMClient`
([`ADR-dreaming-003`](ADR-dreaming-003-consolidation-llmclient.md) makes that free).
**All persistence-side trust policy** (whether/where memories are stored, retention,
encryption at rest) is **deferred to the storage owner** (see
[`ADR-storage-001`](ADR-storage-001-orchestrator-in-process-library.md) open items).

## Consequences for the build

- **Policy:** redaction is a function in the log adapter, applied to every chunk
  pre-model; patterns are configurable.
