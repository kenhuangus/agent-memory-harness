# Knowledge Base — harness

**Domain owner:** Keith
**First entry:** 2026-06-22

Append-only journal of project-story snapshots for the **harness** workstream.
See [README.md](README.md) for conventions.

---

## 2026-06-22T11:32 — entry 1

**Triggered by:** Initial KB seeding via cross-cutting `/kb all` run — establishes baseline state of the harness workstream as the `.kb/` convention lands in the repo.
**Branch:** harness/add-kb-command
**Related ADRs:** ADR-harness-001 through ADR-harness-009
**Cross-domain run:** [KB-storage.md](KB-storage.md), [KB-dreaming.md](KB-dreaming.md), [KB-eval.md](KB-eval.md)

### Summary
The harness workstream owns the model-agnostic memory layer the rest of the project is built around — the Claude Code plugin (`plugin/cookbook_memory/`), the MCP/hooks/skills surface, the log adapter, and the integration seam that lets a coding agent recall persisted memory and append new ones without knowing the storage backend. As of this entry, the walking skeleton (PR #22) has landed, the recall-only conscious surface decision (ADR-harness-008) has been implemented as a `MemoryClient` with client-agnostic skills materialized per-adapter at build time (ADR-harness-009, PR #51), and the v1 functional loop is closed end-to-end via the Stop-hook plugin shim that fires the daydream-cli on session-end (PR #48, joint with dreaming).

### Key state
The conscious surface is **recall-only** (ADR-harness-008) — `recall` is exposed as an MCP tool, but writes happen out-of-band through the Daydreamer, not through an MCP `remember` call. ADR-harness-002 (the original recall/remember pair) is superseded by 008. Skills are **client-agnostic** with one canonical source under `plugin/cookbook_memory/skills/` and per-adapter native bundles materialized by a build step (ADR-harness-009), so Claude Code, OpenCode, and any future adapter install from the same canonical skill. Every hook and tool is **fail-open** (ADR-harness-006) — the memory layer is never allowed to break the user's session. The MCP startup race that gave 40% first-try recall was closed in PR #30 with a priming turn; first-try recall is now 100%. The plugin ships as a build artifact (`plugin/cookbook_memory/adapters/claude_code/`) including `.mcp.json`, hooks_handler, and the recall skill bundle.

### Open items
- The OpenCode adapter is not yet implemented — the `plugin/cookbook_memory/adapters/` directory currently materializes only the Claude Code adapter. The canonical-skill build mechanism (ADR-harness-009) was designed so OpenCode plugs in without changing the skill source, but no `adapters/opencode/` exists yet.
- The structured memory-events stream (ADR-harness-007, Langfuse-bound) is accepted in principle; the events shim currently lives under dreaming (`eval/memeval/dreaming/events.py`) as a no-op + local JSONL diary per ADR-dreaming-009 until the harness-bound observability layer ships.
- The PreCompact hook concurrency contract with Stop (ADR-dreaming-017) is implemented but cross-validation across the two hooks is still informal — no automated test confirms they don't race in a real Claude Code session under load.

### Artifacts at time of entry
- [`architecture.md`](../architecture.md)
- [`prd.md`](../prd.md)
- [`plan.md`](../plan.md)
- [`plugin/README.md`](../plugin/README.md)
- [`plugin/docs/walking-skeleton.md`](../plugin/docs/walking-skeleton.md)
- [`plugin/cookbook_memory/`](../plugin/) — the plugin package source
- [`docs/adrs/ADR-harness-001-claude-code-plugin-shape.md`](../docs/adrs/ADR-harness-001-claude-code-plugin-shape.md)
- [`docs/adrs/ADR-harness-002-recall-remember-mcp-tools.md`](../docs/adrs/ADR-harness-002-recall-remember-mcp-tools.md) (superseded by 008)
- [`docs/adrs/ADR-harness-003-log-extraction-chunking.md`](../docs/adrs/ADR-harness-003-log-extraction-chunking.md)
- [`docs/adrs/ADR-harness-004-dream-state-sidecar.md`](../docs/adrs/ADR-harness-004-dream-state-sidecar.md)
- [`docs/adrs/ADR-harness-005-log-adapter-redaction.md`](../docs/adrs/ADR-harness-005-log-adapter-redaction.md)
- [`docs/adrs/ADR-harness-006-fail-open.md`](../docs/adrs/ADR-harness-006-fail-open.md)
- [`docs/adrs/ADR-harness-007-memory-events-stream.md`](../docs/adrs/ADR-harness-007-memory-events-stream.md)
- [`docs/adrs/ADR-harness-008-recall-only-conscious-surface.md`](../docs/adrs/ADR-harness-008-recall-only-conscious-surface.md)
- [`docs/adrs/ADR-harness-009-client-agnostic-skills.md`](../docs/adrs/ADR-harness-009-client-agnostic-skills.md)
