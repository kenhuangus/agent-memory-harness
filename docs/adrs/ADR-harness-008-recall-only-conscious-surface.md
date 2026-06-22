---
id: ADR-harness-008
domain: harness
title: The conscious surface is recall-only; writes happen via the Daydreamer
status: Accepted
date: 2026-06-21
contract: true
supersedes: ADR-harness-002
superseded_by: none
owner: Keith (P1)
origin: design session 2026-06-21
---

# ADR-harness-008: The conscious surface is recall-only; writes happen via the Daydreamer

**Status:** Accepted · **Date:** 2026-06-21 · **Contract:** yes
**Supersedes:** [`ADR-harness-002`](ADR-harness-002-recall-remember-mcp-tools.md) · **Superseded by:** none

## Context
[`ADR-harness-002`](ADR-harness-002-recall-remember-mcp-tools.md) exposed both
`recall` and `remember` as model-callable MCP tools. The 2026-06-21 design session
revised this: memory **creation** is the Daydreamer's job, not the conscious agent's.
The Daydreamer ([`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md))
always watches the session feed and extracts memories asynchronously, so the
conscious agent does not need — and should not have — an in-loop write tool. Having
the model decide when to `remember` duplicates the Daydreamer's responsibility and
splits the write path across two owners.

## Options considered
- **Recall-only conscious surface** (chosen): the MCP server exposes `recall` only;
  all writes flow through the Daydreamer (the dreaming workstream).
- Keep both `recall` and `remember` as MCP tools (ADR-harness-002): two write paths
  (model-in-loop + Daydreamer) to keep consistent; the model spends tool calls
  deciding what to save, which the Daydreamer already does from the transcript.

## Decision
**The conscious agent is recall-only.** The plugin's MCP server exposes a single
model-callable tool, `recall`. Memory creation happens exclusively in the Daydreamer,
asynchronously. A human-facing `memory-cli remember` command remains for manual/debug
writes, but it is not part of the model's in-loop surface.

## Rationale
One owner for writes (the Daydreamer) means one place for dedup, redaction, and
extraction policy — no split-brain between an in-loop `remember` and the Daydreamer.
The model's only memory concern in-loop is *retrieval*, which is the uniform,
cross-harness primitive ([`ADR-harness-002`](ADR-harness-002-recall-remember-mcp-tools.md)
rationale still holds for `recall`). It also shrinks the conscious surface to exactly
what every harness supports identically.

## Tradeoffs & risks
A memory the user explicitly wants saved mid-session isn't written until the
Daydreamer runs (no synchronous in-loop save). Accepted: the Daydreamer watches the
feed continuously, and `memory-cli remember` covers the rare manual case. If a
synchronous save proves necessary, a future ADR can reintroduce a constrained
`remember` tool.

## Consequences for the build

- **Contract — source of truth:** the MCP tool signatures.
- **Shape:** `recall(query: str, k: int = 5) -> list[{id, content, score, tokens, rank}]`
  is the **only** model-callable tool. There is no `remember` MCP tool and no
  remember skill.
- **Policy:** memory creation is the Daydreamer's
  ([`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md)) /
  ([`ADR-dreaming-002`](ADR-dreaming-002-dreaming-consolidation-cli.md)); the plugin's
  conscious surface never writes.
- **Exhaustive consumers:** the CC `.mcp.json` (and future OpenCode/Codex adapter
  configs) register `recall` only. `RetrievedItem.tokens` must still be populated so
  the eval efficiency metric works.
