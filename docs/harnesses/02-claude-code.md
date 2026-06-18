# Claude Code — Memory Extension Surface

> Can Claude Code host the same memory-plugin core as OpenCode? **Mostly yes**, via
> a single installable plugin bundling an MCP server + hooks, with one architectural
> gap (no per-model-call injection). Sources: `code.claude.com/docs`
> (hooks, plugins, mcp, sub-agents, skills, headless), current as of 2026-06.

## Capability summary

| Capability | Mechanism | Supported | Notes |
|---|---|---|---|
| Pre-gen injection (per user prompt) | `UserPromptSubmit` → `additionalContext`; `SessionStart` (incl. `compact`) | Yes | injected as a user-turn message, **not** the system prompt |
| Pre-gen injection (per model call in tool loop) | — | **No** | no `BeforeModelCall` hook; `UserPromptSubmit` fires once per user prompt |
| Native tools (`recall`/`remember`) | **MCP server** (only path), bundled in plugin `.mcp.json` | Yes | model calls `mcp__memory__recall` etc. |
| Post-tool observation | `PostToolUse` (input+output; can add context / modify output), `PostToolBatch` | Yes | full data to decide what to remember |
| Post-turn observation | `Stop` (re-enters conversation), `SubagentStop` | Yes | `async: true` for fire-and-forget |
| Lifecycle / dreaming triggers | `SessionStart`, `PreCompact` (can block), `PostCompact`, `SessionEnd` | Yes | richest of the three harnesses |
| Config enablement | plugin (`.claude-plugin/`) bundling hooks + MCP; `settings.json` | Yes | one installable plugin covers all of it |
| Per-run store isolation | `env` in `settings.json`; `session_id` per hook; `CLAUDE_ENV_FILE` | Yes | point at a fresh `MEMORY_STORE` per run |
| Headless drive | `claude -p --output-format stream-json`; `--bare` for clean isolation | Yes | hooks + MCP fire unless `--bare` |

## 1. Pre-generation injection

Two near-misses; **no per-model-call hook**:

- **`UserPromptSubmit`** (30s timeout) — fires when the user submits a prompt.
  stdout (or `hookSpecificOutput.additionalContext`) is prepended as context for
  that turn. Fires **once per user prompt**, not before each model call in an
  agentic tool loop. Cannot edit the prompt text; cannot touch the system prompt.

  ```json
  { "hookSpecificOutput": { "hookEventName": "UserPromptSubmit",
      "additionalContext": "MEMORY: …top-k retrieved…" } }
  ```

- **`SessionStart`** with matcher `startup|resume|clear|compact` — stdout added to
  context. The documented way to **re-inject memory after compaction** (`compact`
  matcher).

- **CLAUDE.md / skills** support `!`command`` dynamic substitution, but at
  load/invocation time, not per turn.

**Gap:** there is no `BeforeModelCall`/`PreGenerate` hook. `additionalContext` is a
user-turn message, not a system-prompt edit. → Lean on the model-pulled `recall`
MCP tool for mid-loop retrieval.

## 2. Native tool registration (MCP)

MCP is the **only** way to add model-callable tools. Bundle it in the plugin:

```json
// memory-plugin/.mcp.json
{ "mcpServers": {
    "memory": { "type": "stdio",
      "command": "${CLAUDE_PLUGIN_DIR}/bin/memory-server.py" } } }
```

Registers `mcp__memory__recall(query)` and `mcp__memory__remember(content)`.
Transports: `stdio`, `http`, `sse`, `ws`. Permissions via
`permissions.allow: ["mcp__memory__recall", …]`.

## 3. Post-tool / post-turn observation

- **`PostToolUse`** — after each successful tool. Input: `tool_name`, `tool_input`,
  `tool_output`. Can return `additionalContext` and `updatedToolOutput`. Primary
  "what happened" signal.
- **`PostToolBatch`** — after a parallel tool batch.
- **`Stop`** — after Claude finishes a response turn. `async: true` → fire-and-forget
  consolidation. (Note: `additionalContext` on `Stop` *re-enters* the conversation
  as a new user turn — use carefully to avoid loops.)
- All hooks get `transcript_path` (full JSONL) for richer extraction.

## 4. Lifecycle events for dreaming

| Hook | Fires | Blocking | Memory use |
|---|---|---|---|
| `SessionStart` | start / resume / clear / compact | no | load memories; re-inject post-compaction |
| `PreCompact` | before compaction | **yes** | extract facts before detail is summarized away |
| `PostCompact` | after compaction | no | post-compaction cleanup |
| `Stop` | after each response turn | yes* | trigger dreaming (`async: true`) |
| `SessionEnd` | session terminates | no | final flush / consolidation |

`PreCompact` + `SessionEnd` make Claude Code the **richest** dreaming-trigger
surface of the three.

## 5. Plugin packaging (one installable unit)

```
memory-plugin/
├── .claude-plugin/plugin.json     # name, description, version, author…
├── .mcp.json                      # the recall/remember MCP server
├── hooks/hooks.json               # SessionStart, UserPromptSubmit, PostToolUse,
│                                   #   Stop(async), PreCompact, SessionEnd
├── skills/{recall,remember}/SKILL.md
├── agents/memory-consolidator.md
└── bin/…                          # executables (PATH-injected)
```

`plugin.json` is metadata only; capabilities come from the directory presence.
Hook commands use `${CLAUDE_PLUGIN_DIR}`.

**Limitations to know:**
- Plugin-bundled **subagents cannot** declare `hooks` / `mcpServers` /
  `permissionMode` frontmatter (security). Users copy the agent to
  `.claude/agents/` if they need those.
- A plugin's own `settings.json` supports only `agent` / `subagentStatusLine` — put
  hooks in `hooks/hooks.json`, env in the project `settings.json`.

## 6. Headless & isolation (for the test harness)

```python
subprocess.run(["claude", "-p", "<task>",
    "--output-format", "stream-json",
    "--allowedTools", "mcp__memory__recall,mcp__memory__remember,Read,Edit,Bash"],
    env={**os.environ, "MEMORY_STORE": "/runs/v1.2/store.db"})
```

- All hooks + MCP fire in headless mode **unless `--bare`**.
- `--bare` disables hooks/skills/plugins/MCP/CLAUDE.md → clean baseline runs.
- Per-version isolation: set `MEMORY_STORE` per run (fresh path = empty store).
- The **Agent SDK** (separate package) does **not** expose hooks as
  Python callbacks — hooks are CLI/session-level. For the harness, drive `claude -p`
  and parse `stream-json`.

## Verdict

Claude Code **can host the shared memory core**: MCP server for `recall`/`remember`,
`PostToolUse`/`Stop` for observation, `PreCompact`/`SessionEnd` for dreaming, all
bundled as one plugin and config-isolated by `MEMORY_STORE`. The single real gap vs
OpenCode is **no per-model-call forced injection** — mitigated by making the
model-pulled `recall` tool the primary retrieval path and using `UserPromptSubmit`
for supplementary per-prompt injection. See the
[comparison](01-cross-harness-comparison.md) for the shared-core design.
