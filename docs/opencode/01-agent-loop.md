# OpenCode Agent Loop

> How OpenCode runs one user message → model call → tool execution → next model
> call, and where our memory harness can hook in `retrieve → generate → tool →
> remember`. Source repo: `../opencode` (TypeScript / Bun monorepo). Researched
> against the `dev` branch.

## 1. The main agent / session loop

The authoritative loop is `SessionPrompt.runLoop`:

- `packages/opencode/src/session/prompt.ts:1134` — `runLoop(sessionID)`

Call chain:

1. **User submits a message** → `SessionPrompt.prompt()` (`prompt.ts:1105`) saves
   the user message + parts, then calls `loop()` (`prompt.ts:1123`).
2. **`loop()`** (`prompt.ts:1404`) calls `state.ensureRunning()`, which calls
   `runLoop()` (`prompt.ts:1407`).
3. **`runLoop()`** is a `while (true)` loop (`prompt.ts:1141`). Each iteration:
   - Loads filtered history: `MessageV2.filterCompactedEffect(sessionID)` (`prompt.ts:1145`).
   - Checks exit conditions (last assistant finished with no tool-calls) (`prompt.ts:1164–1182`).
   - Resolves the model, agent, and tools for this step.
   - Creates a new `SessionV1.Assistant` message record (`prompt.ts:1239–1254`).
   - Creates a `SessionProcessor` via `processor.create(...)` (`prompt.ts:1266`).
   - Calls `handle.process(streamInput)` (`prompt.ts:1336`) — issues the LLM call
     and drains the stream.
   - If the result is `"continue"` (more tool calls to run), loops; `"stop"` /
     `"break"` exits.

The per-turn stream is drained by the processor:

- `packages/opencode/src/session/processor.ts:960` — `SessionProcessor.process(streamInput)`
  calls `llm.stream(streamInput)` (`processor.ts:974`), drains via
  `Stream.tap(handleEvent)` → `Stream.runDrain` (`processor.ts:976–979`).
- `handleEvent` (`processor.ts:371`) dispatches each `LLMEvent`: reasoning, text,
  tool-input, tool-call, tool-result, step-start, step-finish. Returns
  `"compact" | "stop" | "continue"` (`processor.ts:1030–1032`).

## 2. How a model turn ("generation") is issued

- `packages/opencode/src/session/llm.ts:357` — `LLM.stream(input)` → `run(input)` (`llm.ts:85`).
- `run()` calls `LLMRequestPrep.prepare(...)` (`llm.ts:106`).
- **Request preparation:** `packages/opencode/src/session/llm/request.ts:56` —
  `LLMRequestPrep.prepare()`:
  - Assembles `system[]` = provider base prompt (e.g. `PROMPT_ANTHROPIC`) + the
    agent's optional `prompt` + any `input.system[]` from `runLoop`
    (`request.ts:58–66`).
  - Fires the **`experimental.chat.system.transform`** plugin hook (`request.ts:69`)
    — plugins may mutate `system[]`.
  - Prepends system strings as `{ role: "system", content }` (`request.ts:105–112`).
  - Fires **`chat.params`** (`request.ts:114`) and **`chat.headers`** (`request.ts:134`).
- Actual LLM call: `streamText(...)` (Vercel AI SDK) (`llm.ts:280`) or native runtime (`llm.ts:227`).
- In `runLoop` just before `handle.process` (`prompt.ts:1327–1345`) the caller builds:
  - `skills` ← `sys.skills(agent)`
  - `env` ← `sys.environment(model)` (cwd, platform, date)
  - `instructions` ← `instruction.system()` (AGENTS.md / CLAUDE.md)
  - `modelMsgs` ← `MessageV2.toModelMessagesEffect(msgs, model)` (full history)
  - `system = [...env, ...instructions, ...(skills ? [skills] : [])]`
  - passed as `system` into `handle.process(streamInput)` → `LLM.stream()`.

## 3. Tools: definition, registration, execution, result feedback

- **Definition:** `packages/opencode/src/tool/tool.ts:151` — `Tool.define(id, init)`
  produces a `Tool.Info` `{ id, init() }`; each tool has Schema-typed `parameters`
  and `execute(args, ctx)`.
- **Registration:** `packages/opencode/src/tool/registry.ts:83` —
  `ToolRegistry.layer` collects builtins (read, glob, grep, edit, write, task,
  fetch, todo, search, skill, patch, question, lsp, plan, shell…) plus custom
  tools from config dirs and plugins. `ToolRegistry.tools({ providerID, modelID,
  agent })` (`registry.ts:267`) filters by context and fires `tool.definition`
  hooks per tool.
- **Wiring into the LLM call:** `packages/opencode/src/session/tools.ts:24` —
  `SessionTools.resolve(...)` wraps each tool in an AI SDK `tool({ description,
  inputSchema, execute })`. The `execute` wrapper (`tools.ts:83`) fires
  `tool.execute.before` (`tools.ts:87`), calls `item.execute(args, ctx)`
  (`tools.ts:93`), fires `tool.execute.after` (`tools.ts:102`), returns the result.
  MCP tools are wrapped the same way (`tools.ts:117–201`).
- **Result feedback:** the AI SDK appends each tool result to the message array and
  continues the stream. The result surfaces as a `tool-result` event that
  `handleEvent` catches (`processor.ts:549`), calling `completeToolCall()`
  (`processor.ts:645`) which writes it to the part store. Next `runLoop` iteration
  re-reads history from the DB via `MessageV2.filterCompactedEffect`.

## 4. System prompt / context assembly (System Context Registry)

`CONTEXT.md` (repo root) describes a formal **System Context Registry**. See
[`03-context-and-compaction.md`](03-context-and-compaction.md) for the full map.
Summary of what populates `system[]` each turn (`prompt.ts:1327–1335`):

- `sys.environment(model)` (`packages/opencode/src/session/system.ts:55`) — cwd,
  worktree, git status, platform, date, reference dirs.
- `sys.skills(agent)` (`system.ts:94`) — available skills.
- `instruction.system()` (`packages/opencode/src/session/instruction.ts:155`) —
  AGENTS.md, CLAUDE.md, (deprecated) CONTEXT.md, remote URL instructions.

The newer structured mechanism lives in `packages/core/src/system-context/` and
`packages/core/src/instruction-context.ts`; the main loop still also assembles a
raw `system[]` string array. The two coexist.

## 5. Hook points for memory integration

| Hook | File:Line | Direction | Use |
|---|---|---|---|
| `experimental.chat.system.transform` | `session/llm/request.ts:69` | **Pre-generation** | Prepend a `<memory>…</memory>` block onto `system[]`. Fires every LLM call (incl. compaction subagents — filter by `sessionID` / `agent.mode`). |
| `experimental.chat.messages.transform` | `prompt.ts:1325`, `compaction.ts:360` | **Pre-generation** | Inject a synthetic recall message into history. |
| `tool.execute.after` | `tools.ts:102`, `prompt.ts:374` | **Post-tool** | Read tool output → decide what to remember. |
| `experimental.text.complete` | `processor.ts:810` | **Post-generation** | Observe completed assistant text → summarize/extract. |
| `chat.message` | `prompt.ts:982` | **Pre-loop** | Observe the raw user message → pre-retrieval. |
| `SessionProcessor.process()` wrapper | `processor.ts:960` | **Around generation** | Wrap the whole generate+run cycle (fork-level). |

`step-finish` handling (`processor.ts:693`) is the clean internal boundary where a
turn ends and token counts are computed (not a plugin hook).

### Recommended

- **Retrieve before generation** via `experimental.chat.system.transform`: query
  memory for the session/task and push a terse `<memory>…</memory>` block onto
  `system[]`. Filter to the main agent and skip compaction subagents.
- **Remember after tool results** via `tool.execute.after`: extract noteworthy
  facts (edits, discoveries, errors) and write them (async) to the store.
- **Remember after model text** via `experimental.text.complete`: summarize the
  finalized assistant text for durable storage.
- **Trigger dreaming** via the `event` hook on session signals (idle / compaction),
  so consolidation runs between work batches without any private API.

A plugin is a TS module exporting `(input) => Hooks`; hooks are
`(input, output) => Promise<void>` and may mutate `output`. No new infra is
required — register the plugin via `.opencode/plugin/` or the `plugin` config key.
Per our design ([`05`](05-integration-strategy.md)), **these hooks are the home of
the memory system** as a distributable plugin — it is a native OpenCode capability,
not a harness shim. (The memory engine itself is Python; the resolved pattern is an
**in-process Python library reached via the `memory mcp` stdio server**, with the TS
plugin as thin config + shims — not a local service, not a TS port. See
[`05`](05-integration-strategy.md) §1/§5 and
[`../harnesses/05-plugin-mvp-plan.md`](../harnesses/05-plugin-mvp-plan.md) ADR-P2.)

## 6. Session state / history storage

- SQLite via Drizzle. `MessageTable` (`packages/core/src/session/sql.ts:67`) stores
  message headers (role, agent, model, cost, tokens, finish reason); `PartTable`
  (`sql.ts:81`) stores parts (text, reasoning, tool call/result, step-start/finish, patch).
- **Write:** `session.updateMessage` / `session.updatePart` publish
  `SessionV1.Event.MessageUpdated` / `PartUpdated`, bridged by `EventV2Bridge` to
  the `SessionProjector` (core) which does the actual inserts.
- **Read history:** `MessageV2.filterCompactedEffect(sessionID)`
  (`message-v2.ts:585`) applies compaction filtering, returns `WithParts[]` — called
  at the top of every `runLoop` iteration.
- **Serialize to model messages:** `MessageV2.toModelMessagesEffect(msgs, model)`
  (`message-v2.ts:142`) via the AI SDK's `convertToModelMessages`.
