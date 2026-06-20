---
id: ADR-storage-001
domain: storage
title: Orchestrator is an in-process library; store-by-path
status: Accepted
date: 2026-06-19
contract: true
supersedes: none
superseded_by: none
owner: Brent (P3)
origin: docs/harnesses/05-plugin-mvp-plan.md (ADR-P2)
---

# ADR-storage-001: Orchestrator is an in-process library; store-by-path

**Status:** Accepted · **Date:** 2026-06-19 · **Contract:** yes
**Supersedes:** none · **Superseded by:** none

## Context
The whiteboard makes the Orchestrator the sole owner of `Mem` and the waist all
memory R/W passes through. The question is whether "Orchestrator" is a *process*
everyone calls or a *library* each client runs in-process over a shared store.

## Options considered
- **In-process library + shared store path:** MCP server, Daydreamer, and Dream
  each construct `MemoryFramework(store=SqliteVectorStore($MEMORY_STORE), router=…)`
  and call it. "Through the Orchestrator" is a *code* waist; the store file + SQLite
  WAL is the cross-process coordination point.
- **Standalone Orchestrator service:** one process literally owns the store + the
  dedup / recently-written-ID cache; everyone RPCs in.

## Decision
**In-process library.** No daemon. The store file at `$MEMORY_STORE` (WAL mode) is
the coordination point.

## Rationale
A standalone service re-introduces an **unmanaged daemon lifecycle** — and Codex,
the floor we design to, has no session-end signal to clean one up. A library keeps
per-run isolation free (`$MEMORY_STORE`), keeps the Orchestrator logic in one class
(`MemoryFramework`, which already *is* a `MemoryStore`), and is the simplest thing
that honors the board's *code* waist. The diagram's "single waist" is preserved as
the rule **all persistence goes through `MemoryFramework`**, not as a single OS
process.

## Tradeoffs & risks
"Single owner of state" becomes logical, not physical: two processes (the MCP
server and the `Stop`-fired dream pass) hold their own Orchestrator over the same
file, so write-dedup consistency leans on the store (SQLite transaction + WAL),
not an in-RAM lock. The meeting's "recently-written-ID cache" is therefore
per-client (carried in the Daydreamer's sidecar — see
[`ADR-harness-004`](ADR-harness-004-dream-state-sidecar.md)), not a global in-RAM
cache. **Requires SQLite WAL** so the MCP writer and the dream reader don't block
each other.

## Consequences for the build

- **Contract — source of truth:** the Orchestrator interface is the existing frozen
  `MemoryStore` protocol (`eval/memeval/protocols.py`) — `write(item)`, `get(id)`,
  `search(query, k, as_of)`, `all()` — as realized by `MemoryFramework`.
- **Shape:** `MemoryFramework(*, router, backends|store, dreamer)`; `write` returns
  the new/merged `item_id` (the meeting's "returns memory ID on every write").
- **Exhaustive consumers** that must construct/call the Orchestrator identically:
  the MCP server ([`ADR-harness-002`](ADR-harness-002-recall-remember-mcp-tools.md)),
  Daydreaming ([`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md)),
  Dreaming ([`ADR-dreaming-002`](ADR-dreaming-002-dreaming-consolidation-cli.md)),
  and the `memory` CLI.
- **Policy:** every client opens the store via `$MEMORY_STORE`; WAL is mandatory.

## Open items (storage-owned)
- **Persistence-side trust policy:** local-only storage for MVP, retention,
  encryption at rest, dedup behaviour on `remember`. (The plugin owns redaction
  *before* the model call — see
  [`ADR-harness-005`](ADR-harness-005-log-adapter-redaction.md) — the Orchestrator
  owns everything once content is persisted.)
- **Dedup-on-write confidence threshold:** the exact threshold and
  merge-vs-new-version policy live in the Orchestrator.
