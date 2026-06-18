# OpenCode Extension Surfaces

> Every supported way to extend or observe OpenCode, ranked for a **cross-language
> (Python) memory layer**. Source repo: `../opencode`.

## 1. Plugin system + lifecycle hooks

A plugin is an async function:

```ts
// packages/plugin/src/index.ts:74
export type Plugin = (input: PluginInput, options?: PluginOptions) => Promise<Hooks>
```

`PluginInput` (`packages/plugin/src/index.ts:56–66`) provides: `client` (typed
`OpencodeClient` for the running server), `project`, `directory`, `worktree`,
`experimental_workspace.register`, `serverUrl: URL`, `$` (Bun shell).

The returned `Hooks` interface (`index.ts:222–335`) — all optional:

| Hook | Fires | Mutable |
|---|---|---|
| `event` | every EventV2 event for this directory | no |
| `config` | config loaded/reloaded | no |
| `tool` | registration of extra model-callable tools | — |
| `auth` / `provider` | OAuth / dynamic model listing | — |
| `chat.message` | before a new user message is processed | yes (`message`, `parts`) |
| `chat.params` | before LLM call | yes (temp, topP, topK, maxOutputTokens, options) |
| `chat.headers` | before LLM call | yes (headers) |
| `permission.ask` | permission gate | yes (allow/deny) |
| `command.execute.before` | before a slash command | yes |
| `tool.execute.before` | before any tool (builtin or MCP) | yes (`args`) |
| `tool.execute.after` | after a tool completes | yes (`title`, `output`, `metadata`) |
| `shell.env` | bash tool env setup | yes |
| `experimental.chat.messages.transform` | replace the message history sent to LLM | yes |
| `experimental.chat.system.transform` | add to / replace the system prompt | yes |
| `experimental.provider.small_model` | override the small model | yes |
| `experimental.session.compacting` | customize compaction prompt | yes |
| `experimental.compaction.autocontinue` | disable auto-continue after compaction | yes |
| `experimental.text.complete` | after text generation | yes (text) |
| `tool.definition` | modify tool description/parameters sent to LLM | yes |
| `dispose` | teardown | — |

Trigger sites: `tool.execute.before/after` → `session/tools.ts:87–147`;
`experimental.chat.system.transform` → `session/llm/request.ts:69`; `chat.params`
/ `chat.headers` → `session/llm/request.ts:114,134`.

**Loading** (`packages/opencode/src/plugin/index.ts:280–293`): `Plugin.trigger`
iterates loaded hook objects and `await fn(input, output)` for each. Local files
are auto-discovered from `.opencode/plugin(s)/*.{ts,js}`; npm modules are added via
`opencode plugin <module>` (patches the `plugin` config array).

**Limitation:** plugins are TypeScript/JS only. There is **no remote-plugin
protocol** — to call Python from a hook you need a thin TS shim talking to your
Python process over HTTP/stdio.

## 2. Event bus

Two layers:

- **Internal EventV2** (`packages/core/src/event.ts:147–173`): typed in-process
  PubSub; `events.listen(fn)`. Not accessible outside the process.
- **GlobalBus** (`packages/opencode/src/bus/global.ts`): a Node `EventEmitter` that
  bridges EventV2 → HTTP SSE. Shape: `{ directory?, project?, workspace?, payload:
  { id, type, properties } }`.

Session events (`packages/core/src/session/event.ts`) include
`session.next.step.started/ended/failed`, `session.next.tool.called/progress/
success/failed`, `session.next.text.started/delta/ended`,
`session.next.prompted/admitted/promoted`,
`session.next.compaction.started/ended`, `mcp.tools.changed`,
`server.connected/heartbeat/instance.disposed`. **External processes can subscribe
via SSE** (§3).

## 3. HTTP / SSE server API

Server: `packages/opencode/src/server/routes/instance/httpapi/`. Default
`127.0.0.1:4096`; actual URL surfaced via `PluginInput.serverUrl`.

**SSE streams:**

| Endpoint | Description | File |
|---|---|---|
| `GET /global/event` | all directories | `groups/global.ts:87` |
| `GET /event?directory=…` | per-directory/workspace; 10s heartbeat; terminal `server.instance.disposed` | `groups/event.ts:14` |

**Session REST** (`groups/session.ts`): `GET/POST /session`, `POST
/session/:id/message` (sync), `POST /session/:id/prompt_async`, `POST
/session/:id/abort`, `GET /session/:id/message[/:msgID]`.

**MCP REST** (`groups/mcp.ts`): `GET /mcp`, `POST /mcp` (add at runtime), `POST
/mcp/:name/connect|disconnect`.

**Config REST** (`groups/config.ts`): `GET /config`, `PATCH /config`.

**Auth:** Basic auth via `OPENCODE_SERVER_PASSWORD` / `OPENCODE_SERVER_USERNAME`
(`server/auth.ts`); none required if password unset. OpenAPI is auto-generated;
Python can consume it with any HTTP client.

## 4. Custom tools at runtime

- **A. Plugin hook (TS):** return `{ tool: { myTool: tool({…}) } }`
  (`tool/registry.ts:188–193`).
- **B. File auto-discovery (TS):** drop `.opencode/tool/*.{ts,js}`
  (`tool/registry.ts:172–186`).
- **C. MCP server (cross-language, incl. Python):** any connected MCP server's
  tools are presented to the LLM like builtins. **The only cross-language path.**

`tool.execute.before/after` apply to MCP tools too (`tools.ts:128–148`).

## 5. MCP integration

OpenCode is a full MCP client (`packages/opencode/src/mcp/index.ts`, using
`@modelcontextprotocol/sdk`). Transports (`core/src/config/mcp.ts`,
`core/src/v1/config/mcp.ts`): `type: "local"` (spawn subprocess over stdio),
`type: "remote"` (StreamableHTTP / SSE, OAuth-capable).

Config (`opencode.json`):

```jsonc
{
  "mcp": {
    "memory": { "type": "local", "command": ["python", "-m", "memory_mcp"],
                "environment": { "MEMORY_DB": "/path" }, "enabled": true, "timeout": 30000 }
  }
}
```

or remote: `{ "type": "remote", "url": "http://localhost:8765/mcp", "headers": { "Authorization": "Bearer …" } }`.

Runtime add (no restart): `POST /mcp` with `{ name, config }` (`groups/mcp.ts:55`).
Tools surface as `{server}_{toolName}`; OpenCode honors `tools/list_changed`.

Python side (official `mcp` SDK / FastMCP):

```python
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("memory")

@mcp.tool()
def recall(query: str) -> str: ...

@mcp.tool()
def remember(content: str, tags: list[str] = []) -> str: ...

if __name__ == "__main__":
    mcp.run()  # stdio
```

## 6. Configuration

Layered (later wins): global `~/.config/opencode/opencode.jsonc` → remote
well-known → project `.opencode/opencode.jsonc` → `OPENCODE_CONFIG_CONTENT` env.
Relevant keys: `mcp`, `plugin` (npm names or local paths), `model`,
`agent.<name>.model`, `tools.<name>` enable/disable. Auto-discovered:
`.opencode/tool/*.{ts,js}`, `.opencode/agent/*.md`. Runtime read/write via
`GET /config` and `PATCH /config`.

## Integration surface options (ranked)

> **Design constraint** ([`05`](05-integration-strategy.md)): memory must be a
> **native OpenCode capability a human developer gets for free**, not a
> harness-only backdoor. That rules out "the harness owns memory and OpenCode is
> just a subprocess it observes." Memory must live *inside* OpenCode's public
> extension surface; the harness then drives OpenCode exactly as a user would.

| Mechanism | How | Native to a dev? | Covers full memory design? | Verdict |
|---|---|---|---|---|
| **OpenCode plugin** | `@cookbook/memory` via `plugin` config / `.opencode/plugin`; hooks + tools | **yes — one config line** | **yes** — `experimental.chat.system.transform` (forced injection), `tool` (recall/remember), `tool.execute.after` / `experimental.text.complete` (observe), `event` (dream triggers) | **Chosen** |
| **Fork / patch core** | memory as a `SystemContext` source + tools in the fork | yes (if dev runs our fork) | yes, plus the snapshot/diff caching nicety a plugin lacks | **Fallback** if we need the diff machinery |
| **MCP server** | `recall`/`remember` as MCP tools in `mcp` config | yes (dev enables MCP) | tools only — no pre-generation injection, no `event`-driven dreaming | Possible *complement* for the tool path; not sufficient alone |
| **Harness-only: subprocess + SSE observer** | Python drives `opencode run` / `/event` and owns memory itself | **no — backdoor** | n/a | **Rejected** — violates the native-capability constraint |

## Summary

Memory must be a **native OpenCode capability**, so it lives inside OpenCode's
public extension surface rather than in the harness. The chosen home is a
**distributable OpenCode plugin**: its hook surface
(`packages/plugin/src/index.ts:222–335`) covers the full design —
`experimental.chat.system.transform` for forced pre-generation injection (the same
`system[]` a fork would feed), the `tool` map for native `recall`/`remember`,
`tool.execute.after` + `experimental.text.complete` + `chat.message` for deciding
what to remember, and the `event` hook for triggering the dreaming worker on
session signals (idle / compaction). The only thing a plugin cannot do that a fork
can is register a *typed* `SystemContext` source with snapshot/diff "Mid-Conversation
System Message" emission — an efficiency optimization, not a capability — so the
memory system is not compromised by choosing the plugin. A **fork** is the
documented fallback if we later need that caching machinery. The harness drives
OpenCode through public actions (`opencode run`, or `serve` + REST/SSE) only to
grade and log; it never reaches into memory. See
[`05`](05-integration-strategy.md) for the full strategy and the four run shapes.
