---
id: ADR-harness-002
domain: harness
title: recall/remember are MCP tools through the Orchestrator
status: Accepted
date: 2026-06-19
contract: true
supersedes: none
superseded_by: none
owner: Keith (P1)
origin: docs/harnesses/05-plugin-mvp-plan.md (ADR-P4)
---

# ADR-harness-002: `recall`/`remember` are MCP tools through the Orchestrator

**Status:** Accepted · **Date:** 2026-06-19 · **Contract:** yes
**Supersedes:** none · **Superseded by:** none

## Context
The model needs native, in-loop memory. MCP is the only path to model-callable
tools in Claude Code, and is the universal substrate across all three harnesses.
Per the board, both read and write go **through the Orchestrator**
([`ADR-storage-001`](ADR-storage-001-orchestrator-in-process-library.md)).

## Options considered
- **`recall` and `remember` both call the Orchestrator** (`MemoryFramework.search`
  / `.write`): router picks the backend on read; dedup-on-write; `remember` returns
  the memory ID.
- Reads bypass the Orchestrator (faster, no routing hop), only writes go through —
  rejected: the board's bidirectional arrow governs, and bypassing loses the
  router's "pick the best backend" on reads.

## Decision
**Both `recall` and `remember` route through the Orchestrator.** The MCP server is
a thin FastMCP wrapper that constructs the Orchestrator
([`ADR-storage-001`](ADR-storage-001-orchestrator-in-process-library.md)) and calls
it.

## Rationale
Keeps one waist (the board), one place for routing/dedup/embeddings, and one place
the `as_of`/`version` invariants live (`architecture.md` §3). The model-pulled
`recall` tool is the **primary** retrieval path because it's the only mechanism
uniform across all three harnesses; `UserPromptSubmit` injection is *supplementary*.

## Tradeoffs & risks
A routing hop on every read (negligible vs. model latency). The MCP process is
long-lived per session — that is MCP's normal model, not a daemon we manage.

## Consequences for the build

- **Contract — source of truth:** the MCP tool signatures.
- **Shape:** `recall(query: str, k: int = 5) -> list[{id, content, score, tokens}]`;
  `remember(content: str, tags: list[str] = []) -> {id: str}`. Both delegate to
  `MemoryFramework`; `remember` returns the Orchestrator's memory ID.
- **Exhaustive consumers:** the CC `.mcp.json`, the OpenCode/Codex adapter configs
  (later), and the subconscious functions — Daydreaming
  ([`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md)) and Dreaming
  ([`ADR-dreaming-002`](ADR-dreaming-002-dreaming-consolidation-cli.md)) — which both
  write via the same Orchestrator, not the MCP tool.
- **Policy:** `remember` is the **in-loop** memory-creation path (the model decides
  to save); **Daydreaming**
  ([`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md)) is the separate
  in-session path that additionally mines the logs for what the model didn't save.
  The iter-2 ramp ("memory on, consolidation off") relies on `remember` + Daydreaming
  working while night Dreaming
  ([`ADR-dreaming-002`](ADR-dreaming-002-dreaming-consolidation-cli.md)) is still a
  no-op.
- **Policy:** `RetrievedItem.tokens` must be populated so the eval efficiency metric
  works (`architecture.md` §3 invariants).
