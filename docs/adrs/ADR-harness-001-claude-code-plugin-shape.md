---
id: ADR-harness-001
domain: harness
title: Claude Code plugin shape (MCP + hooks + skills)
status: Accepted
date: 2026-06-19
contract: false
supersedes: none
superseded_by: none
owner: Keith (P1)
origin: docs/harnesses/05-plugin-mvp-plan.md (ADR-P3)
---

# ADR-harness-001: Claude Code plugin shape (MCP + hooks + skills)

**Status:** Accepted · **Date:** 2026-06-19 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
The conscious surface on the board is "Plugin = skills / MCP / hooks." Claude Code
(see [`docs/harnesses/02-claude-code.md`](../harnesses/02-claude-code.md)) bundles
all of these into one installable plugin.

## Options considered
- **One Claude Code plugin** bundling `.mcp.json` (the memory MCP server),
  `hooks/hooks.json`, and `skills/`. (The documented CC pattern.)
- Hooks-only, or MCP-only — rejected: the board explicitly shows all three, and
  MCP is the *only* path to model-callable tools while hooks are the only path to
  lifecycle observation. They are complementary, not alternatives.

## Decision
**One Claude Code plugin** = bundled MCP server + hooks + skills, under the
memory-system package's `adapters/claude-code/`.

## Rationale
Matches the board and the CC plugin model: one installable unit covers tool
registration (MCP), lifecycle observation (hooks), and human-facing affordances
(skills). Keeping it under `adapters/claude-code/` makes Claude Code explicitly *an
adapter* over the harness-agnostic core, so OpenCode/Codex adapters drop in as
siblings later.

## Tradeoffs & risks
Plugin-bundled subagents can't declare their own `hooks`/`mcpServers` — fine, we
don't need that for MVP. The hook scripts use `${CLAUDE_PLUGIN_DIR}` and must
locate the memory-system entry points (the `memory` console script), which the
package install must put on PATH.

## Consequences for the build

- **Policy — plugin layout:** `.claude-plugin/plugin.json`, `.mcp.json`,
  `hooks/hooks.json`, `skills/{recall,remember}/SKILL.md`.
- **Policy — hooks wired for MVP:** `SessionStart` (init + post-compact memory
  re-inject), `UserPromptSubmit` (supplementary top-k push — see
  [`ADR-harness-002`](ADR-harness-002-recall-remember-mcp-tools.md)), **`Stop`
  (`async`) + `PreCompact` → the Daydreamer day pass**
  ([`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md)), and
  `PostCompact` → re-inject top memories after compaction. `SessionEnd` is
  available for a final flush. `PostToolUse` is available but not required for MVP
  (the Daydreamer reads the transcript, not per-tool hooks). **Night**
  consolidation is the separate public `memory dream --all` CLI, not a hook.
