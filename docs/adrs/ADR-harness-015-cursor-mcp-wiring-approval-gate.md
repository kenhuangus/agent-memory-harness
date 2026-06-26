---
id: ADR-harness-015
domain: harness
title: Cursor MCP wiring — mcp.json + pre-cleared approval/trust gates + stream-json parsing for the recall/remember tools
status: Proposed
date: 2026-06-26
contract: false
supersedes: none
superseded_by: none
owner: Keith (P1)
origin: docs/harnesses/06-cursor-cli.md §2/§6/§7
---

# ADR-harness-015: Cursor MCP wiring + approval/trust gates + stream-json parsing

**Status:** Proposed · **Date:** 2026-06-26 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
For the Cursor adapter ([`ADR-harness-013`](ADR-harness-013-cursor-cli-second-harness.md))
the agent must reach the same memory tools the Claude path uses — `recall` /
`remember` — exposed by the cookbook-memory MCP server, and the harness must parse
the run's output for the answer + token usage. Three Cursor-specific facts (all
verified against the binary in
[`docs/harnesses/06-cursor-cli.md`](../harnesses/06-cursor-cli.md)) shape how:

1. **MCP config is the same `{"mcpServers": …}` schema as Claude Code**, read from
   `<HOME>/.cursor/mcp.json` (global) or `<cwd>/.cursor/mcp.json` (project). So the
   *same* server entry drops in.
2. **There are two approval gates** that block a headless run from using MCP tools:
   (a) **server-load approval** — a configured server starts `not loaded (needs
   approval)`; cleared with `cursor-agent mcp enable <id>` or `--approve-mcps`; and
   (b) **tool-run permission** — the `Mcp(server:tool)` allowlist in
   `cli-config.json`, or `--force`/`--yolo`. Plus a **workspace-trust** gate that
   aborts an untrusted-dir headless run unless `--trust` is passed.
3. **`stream-json` is close to Claude Code's** (`system`/`user`/`assistant`/`result`
   with a `usage` block) but adds `tool_call` and `thinking` event types, and the
   **model-facing MCP tool name is `<server>-<tool>`** (e.g. `memory-recall`) — a
   hyphen form, not Claude Code's `mcp__server__tool`, and version-sensitive.

## Options considered
- **Reuse the shipping cookbook-memory MCP server via `mcp.json`, pre-clear the gates
  in sandbox setup, parse stream-json** (chosen). The server is the harness-agnostic
  core; only the config file, the gate-clearing, and the parser are Cursor-specific.
- **Bundle the server through `--plugin-dir`** instead of `mcp.json`. Deferred:
  `--plugin-dir` is real and attractive (one bundle = MCP + hooks + skills), but
  whether a plugin-bundled MCP server **auto-approves** in headless is UNVERIFIED — it
  may still hit gate (a). `mcp.json` + explicit `mcp enable` is the known-good path
  for MVP; `--plugin-dir` is a follow-up once auto-approval is tested.
- **Drive memory via a hook (`sessionStart` inject) instead of the MCP tool.**
  Rejected as the *primary* path: the model-pulled `recall` tool is the uniform
  cross-harness retrieval path (ADR-harness-008); `sessionStart` injection is a
  supplementary push, tracked separately, not the tool surface this ADR wires.
- **Parse `--output-format json`** (single envelope) instead of `stream-json`.
  Rejected: `stream-json` carries the per-event `tool_call`s the harness needs to
  attribute recalls to the trajectory, and its `result` event already has the `usage`
  totals — one format covers both answer and observation.

## Decision
The Cursor adapter wires memory by writing the **standard cookbook-memory MCP server
entry into `<HOME>/.cursor/mcp.json`**, **pre-clears all gates during sandbox setup**
(`cursor-agent mcp enable <server>` against the sandbox `HOME`, and passes
`--approve-mcps --trust` on every run; `--force` when a plugin stage must let the
tool run without prompts), and **parses `--output-format stream-json`** for the
answer (`result.result`), token usage (`result.usage.{input,output,cache*}Tokens` →
the harness cost/`RetrievedItem.tokens` accounting), and `tool_call` events to
attribute `recall` calls to the trajectory. Tool identity is matched on the
structured `mcpToolCall` fields (`providerIdentifier` + `toolName`), **not** the
joined wire string, to survive Cursor's version-sensitive name format.

## Rationale
This keeps the memory **core** untouched (same MCP server, same `recall`/`remember`)
and confines every Cursor-ism to config + gate-clearing + parsing — the thin-adapter
promise. Pre-clearing the gates is mandatory because a headless run silently gets
**no** memory tools otherwise; doing it against the sandbox `HOME`
([`ADR-harness-014`](ADR-harness-014-cursor-home-isolation-api-key-auth.md)) keeps the
developer's host config untouched. Matching on `(providerIdentifier, toolName)` makes
the recall-attribution robust to the hyphen/underscore churn we already observed
across versions.

## Tradeoffs & risks
- **The approval gate is a real failure mode.** If setup forgets `mcp enable` /
  `--approve-mcps`, the agent runs tool-less and the plugin stage silently looks like
  the baseline. Mitigation: the adapter verifies the server is `ready`
  (`cursor-agent mcp list`) after setup and asserts at least the option to recall is
  present before counting a plugin run as valid (mirrors the Claude path's
  recall-reach check via the events stream).
- **Tool wire-name drift.** `memory-recall` today, possibly `mcp_memory_recall`
  tomorrow. Mitigation: match structured fields, and resolve the live name with
  `cursor-agent mcp list-tools <server>` in a diagnostic.
- **stream-json dialect drift.** Cursor adds `tool_call`/`thinking`; future versions
  may add more. Mitigation: the parser is tolerant (ignores unknown event types,
  reads usage only from `result`), exactly like the Claude parser's schema-variation
  tolerance.
- **`--force`/`--yolo` widens permissions** for plugin stages. Accepted for an eval
  sandbox (isolated `HOME`, throwaway checkout); never used against host config.

## Consequences for the build
- **Policy — mcp.json content:** the adapter writes
  `{"mcpServers": {"<server>": {"command": <plugin-python>, "args": ["-m", "…", …],
  "env": {"MEMORY_STORE": <fresh path>}}}}` into `<HOME>/.cursor/mcp.json`. The server
  is the same cookbook-memory MCP server the Claude path uses; only the file location
  and the `MEMORY_STORE` value are Cursor/run-specific.
- **Policy — gate-clearing in setup (order matters):** set sandbox `HOME` first
  ([ADR-014]), then `cursor-agent mcp enable <server>`; pass `--trust --approve-mcps`
  on every headless run, and `--force` on plugin stages that must auto-run the tool.
  The `base` stage writes **no** memory server into `mcp.json` (provably tool-less).
- **Policy — stream-json parsing:** read the final `result` event for
  `result`/`is_error`/`usage`; sum `usage.{inputTokens,outputTokens,cacheReadTokens,
  cacheWriteTokens}` into the harness token accounting; treat each `tool_call`
  (`mcpToolCall`) whose `providerIdentifier`==the memory server + `toolName`==`recall`
  as a recall step for the trajectory. Unknown event types are ignored.
- **Policy — workspace trust:** always pass `--trust` headlessly (the eval checkout
  is trusted by construction); never depend on an interactive trust prompt.
- **Cross-link:** the backend
  ([`ADR-harness-013`](ADR-harness-013-cursor-cli-second-harness.md)); isolation/auth
  that makes the gate-clearing safe
  ([`ADR-harness-014`](ADR-harness-014-cursor-home-isolation-api-key-auth.md));
  recall-only conscious surface
  ([`ADR-harness-008`](ADR-harness-008-recall-only-conscious-surface.md)).
