# Cursor CLI — Memory Extension Surface

> Can the **Cursor CLI** host the same memory-plugin core as Claude Code / Codex?
> **Yes — and it is the *richest* surface of the four.** It has MCP, a full hooks
> system (≈18 events, on par with or beyond Claude Code), a first-class
> `--plugin-dir` install path, a `stream-json` headless output that is **nearly
> byte-compatible with Claude Code's**, and **`HOME`-based config isolation** plus
> keychain-free **`CURSOR_API_KEY`** auth that together make per-stage parallel runs
> *easier* than Claude-Code-on-macOS (§9). The one real cost is operational, not
> architectural: an **MCP approval gate** that must be pre-cleared for autonomous
> runs.
>
> **Two-tier sourcing.** Most of this doc is **verified against the installed
> binary** — `cursor-agent` **v2026.05.20-2b5dd59** on macOS: its `--help`, the
> `mcp`/`models`/`hooks` surfaces, a real headless `stream-json` run, the bundled
> `create-hook` / `create-skill` skills, and `strings`/grep over the JS bundle for
> env-var and config behavior. Claims I could only get from docs
> (`docs.cursor.com`) or could not verify locally are flagged **[docs]** or
> **UNVERIFIED**. Cursor moves fast — re-verify before building. Owner: **P1
> Keith**.

## Capability summary

| Capability | Mechanism | Supported | Notes |
|---|---|---|---|
| Pre-gen injection (per session, headless) | `sessionStart` hook → `additional_context` | Yes | **fires + injects in headless** (verified). The working inject point for `-p` |
| Pre-gen injection (per user prompt) | `beforeSubmitPrompt` hook | Interactive | does **not** fire in headless `-p`; and it can't inject (output is `continue`/`user_message`, no `additional_context`) |
| Pre-gen injection (per model call in tool loop) | — | **No** | no before-each-model-call hook (same gap as Claude Code / Codex) |
| Native tools (`recall`/`remember`) | **MCP** via `mcp.json` (`{"mcpServers": …}`) — **same schema as Claude Code**; or bundled inside a `--plugin-dir` plugin | Yes | model calls them natively; **gated by an approval list** (see §6) |
| Post-tool observation | `postToolUse` (→ `additional_context`, `updated_mcp_tool_output`), `afterFileEdit`, `afterShellExecution`, `afterMCPExecution` | Yes | full data; can append context + rewrite MCP tool output |
| Post-turn observation | `stop` (→ `followup_message`, `loop_limit`), `afterAgentResponse`, `afterAgentThought` | Interactive | `stop` **does NOT fire in headless `-p`** (verified); use `sessionEnd` (§4) |
| Lifecycle / dreaming triggers | `sessionStart`, `sessionEnd` (headless ✅), `preCompact`, `stop` (interactive only) | Yes | **has a real `sessionEnd`** that fires headless and carries `transcript_path` — unlike Codex |
| Native tools / plugin extension | `--plugin-dir <path>` (repeatable CLI flag) + `.cursor-plugin/plugin.json`; bundles rules · skills · agents · commands · hooks · **MCP servers** | Yes | one installable unit, like a Claude Code plugin; has a marketplace too |
| Config enablement | `mcp.json` + `hooks.json` (project `.cursor/` or user `~/.cursor/`); `cli-config.json` permissions | Yes | |
| Per-run store + config isolation | `MEMORY_STORE` env on the MCP server + **`HOME`** to relocate `~/.cursor/{mcp.json,cli-config.json,auth}` | Yes | fresh `MEMORY_STORE` = empty store; fresh `HOME` = clean config/MCP/auth. **`CURSOR_DATA_DIR` only moves transcripts** (§7/§9) |
| Headless drive | `cursor-agent -p --output-format stream-json --trust` | Yes | **stream-json ≈ Claude Code's** (`system`/`user`/`assistant`/`result` + `usage`) |
| Hook count (rough) | **≈18** events | — | vs Claude Code 29+, Codex ~10 |
| Cross-model | `--model` spans Anthropic (`claude-opus-4-8-thinking-high`), OpenAI (`gpt-5.x-codex…`), Cursor (`composer-2.5`) | Yes | one harness, three vendors — strong fit for the *model-agnostic* goal |

## 1. Pre-generation injection

The injection story is **more constrained in headless than Claude Code**, and this
is the one place the harness must adapt its design:

- **`sessionStart` hook → `additional_context`** — **fires in headless `-p` and the
  injected text DOES reach the model** (verified empirically by a teammate: the
  model answered with a fact that existed *only* in the hook's `additional_context`
  output). This is the **per-session** supplementary push of top-k memories.
- **`beforeSubmitPrompt` hook** — the per-prompt analog (matcher value
  `UserPromptSubmit`; stdin carries `prompt` + `attachments[]`) — but it **does NOT
  fire in headless `-p`** (verified: zero firings). In `-p` the prompt is a CLI arg,
  never "submitted." Its output is `continue`/`user_message` only — **no
  `additional_context`, so it cannot inject even when it does fire** (interactive).
- **Rules** — `.cursor/rules/*.mdc`, plus project-root `AGENTS.md` / `CLAUDE.md`,
  are static/load-time instructions (the AGENTS.md analog). `generate-rule` /
  `cursor-agent rule` scaffolds one.

**Consequence for the harness:** in headless mode there is **no per-turn forced
injection** — injection is **per-session** (`sessionStart`) only. Two clean ways to
live with this: (a) make the model-pulled **`recall` MCP tool the primary retrieval
path** (uniform across all harnesses anyway), with `sessionStart` as a one-shot
top-k push; or (b) **run one prompt per `cursor-agent` invocation** so a session ≈ a
turn and `sessionStart` injection effectively becomes per-turn. The eval harness
already drives one task per invocation, so (b) falls out for free.

## 2. Native tool registration (MCP)

Cursor CLI is an MCP **client**, and its config is the **same `mcp.json` schema as
Claude Code** (verified locally) — so the *same* memory MCP server config drops in
unchanged:

```jsonc
// ~/.cursor/mcp.json (global)  OR  <project>/.cursor/mcp.json (project; project wins)
{ "mcpServers": {
    "memory": {
      "command": "python3",
      "args": ["-m", "cookbook_memory.mcp"],
      "env": { "MEMORY_STORE": "/runs/v1.2/store.db" }
    }
    // HTTP/SSE: { "type": "http", "url": "https://…", "headers": { … } }
} }
```

Management subcommands (verified): `cursor-agent mcp list | list-tools <id> |
enable <id> | disable <id> | login <id>`. The model then calls the server's tools
natively. MCP servers can **also** be bundled inside a plugin (the bundle exposes a
`reloadPluginMcpServers` path) — see §5.

**Tool wire-name format (verified by stand-up):** I registered a stub `memory`
server exposing `recall`/`remember` and drove a real headless turn. The model calls
the tool as **`memory-recall`** — i.e. **`<server>-<tool>`, hyphen-joined**, *not*
Claude Code's `mcp__<server>__<tool>`. The stream-json `tool_call` event also
exposes the parts separately:

```jsonc
{ "type": "tool_call", "subtype": "started",
  "tool_call": { "mcpToolCall": { "args": {
      "name": "memory-recall",          // ← the model-facing wire name
      "providerIdentifier": "memory",   // ← server id
      "toolName": "recall",             // ← tool id
      "args": { "query": "project deadline" } } } },
  "session_id": "…" }
```

→ The eval permission allowlist and any name-matching key on **`memory-recall`** /
(`providerIdentifier`, `toolName`). `cursor-agent mcp list-tools memory` shows the
bare names (`recall (query, k)`, `remember (content)`) — verified.

> **Version-sensitivity warning.** This `<server>-<tool>` hyphen form is what *this*
> binary (v2026.05.20→v2026.06.24) emitted in the `tool_call` event. Cursor forum
> bug reports describe an older single-underscore form `mcp_<server>_<tool>`
> (e.g. `mcp_cobrowser_tool_x`), and Claude Code uses double-underscore
> `mcp__<server>__<tool>` — **all three differ.** Don't hardcode the format;
> resolve it per-version with `cursor-agent mcp list-tools <server>` and prefer
> matching the structured `(providerIdentifier, toolName)` fields over the joined
> string. **Note also:** the model-facing tool name (hyphen/underscore) is a
> *different* string from the **permission token** `Mcp(server:tool)`, which always
> uses a **colon** (§6).

## 3. Post-tool / post-turn observation

Cursor's hook surface here is the **richest of the four harnesses** (verified from
the bundled `create-hook` skill):

- **`postToolUse`** — after each tool. Can return **`additional_context`** and, for
  MCP tools, **`updated_mcp_tool_output`** (rewrite a tool's output). Plus
  `afterFileEdit`, `afterShellExecution`, `afterMCPExecution` for typed variants.
- **`stop`** — after the agent finishes a turn. Can return a **`followup_message`**
  (re-enters the loop; bounded by `loop_limit`) — use sparingly. **Interactive
  only** — does **not** fire in headless `-p` (§4); use `sessionEnd` headlessly.
- **`afterAgentResponse` / `afterAgentThought`** — observe assistant output /
  reasoning. No direct analog in the other harnesses; `afterAgentResponse` also does
  **not** fire in headless `-p` (verified).
- All command hooks exchange **JSON over stdin/stdout**; exit `2` blocks the action,
  other non-zero fails *open* unless `failClosed: true`.

## 4. Lifecycle events for dreaming

**Hooks fire in headless `-p/--print` mode** — empirically verified with an
isolated sandbox + a logging hook (a real `cursor-agent -p … --trust` run). What
fired, and what did not, in headless:

| Hook | Headless `-p`? | Memory use |
|---|---|---|
| `sessionStart` | ✅ fires (verified) | load memories; (re-)inject. Payload has `session_id`/`conversation_id`, `model`, `workspace_roots`; `transcript_path` is `null` (none yet) |
| `postToolUse` | ✅ fires on a tool-using turn (verified) | observe tool results; `additional_context` / `updated_mcp_tool_output` |
| `preCompact` | (untested — not reached in a short run) | extract facts before detail is summarized away |
| `stop` | ❌ **did NOT fire** in `-p`, even on a tool-using turn (verified) | interactive-only signal; **do not anchor headless dreaming on it** |
| `sessionEnd` | ✅ fires (verified) | **the turn-complete / dreaming trigger for headless.** Payload carries **`transcript_path`**, `reason`/`final_status` (`completed`), `duration_ms`, `session_id` |

**The load-bearing finding for dreaming:** in headless mode the trigger is
**`sessionEnd`**, not `stop`. Its payload includes a real **`transcript_path`** —
a JSONL log at
`~/.cursor/projects/<workspace-slug>/agent-transcripts/<sid>/<sid>.jsonl` with
`{role, message}` lines + a `turn_ended` marker (verified) — i.e. exactly the
`transcript_path` + log-source the Daydreamer needs, the direct analog of Claude
Code's `transcript_path`. Unlike **Codex** (no true session-end), Cursor gives us a
real one **and** it fires headless, so the Codex-floor self-backgrounding is **not
required** for Cursor (it still works as a fallback). Note `session_id ==
conversation_id ==` the `--resume`/`--continue` chat id.

> **Caveat — still verify:** `preCompact` firing in `-p` is untested (the short run
> never compacted), and whether self-spawned background work from a `sessionEnd`
> hook survives the parent `cursor-agent` exit needs a dedicated test. Anchor on
> `sessionEnd` for the trigger; keep the self-backgrounded path as the fallback if
> the child is reaped on exit.

### Hook stdin/stdout contract (for the adapter)

Every command hook gets JSON on stdin and returns JSON on stdout (exit `2` blocks;
non-zero fails *open* unless `failClosed: true`). **Common base fields** (verified
live for `sessionStart`/`sessionEnd`; documented for the rest): `conversation_id`,
`generation_id`, `model`, `hook_event_name`, `cursor_version`, `workspace_roots`,
`user_email`, `transcript_path`. The docs key on `conversation_id` (not
`session_id`); the runtime *adds* `session_id` on the events that fire headless
(`conversation_id == session_id` in practice). Use `transcript_path` +
`conversation_id` as the stable join key. Per-event specifics the adapter needs:

| Event | Key stdin fields | Output the adapter uses |
|---|---|---|
| `sessionStart` | base (`transcript_path: null` at start) | **`additional_context`** (inject memories) |
| `postToolUse` | base + tool name/input/output | `additional_context`, `updated_mcp_tool_output` |
| `sessionEnd` | base + `reason`, `final_status`, `duration_ms` | (fire-and-forget) → kick the dreaming pass off `transcript_path` |
| `beforeSubmitPrompt`* | base + `prompt`, `attachments[]` | `continue`, `user_message` (no inject) |
| `stop`* | base + `status`, `loop_count` | `followup_message` (bounded by `loop_limit` config) |

\* interactive-only — does not fire in headless `-p`.

## 5. Plugin packaging (`--plugin-dir`)

Cursor CLI has a **first-class local-plugin flag** (verified): `--plugin-dir <path>`
(repeatable). A plugin is a directory with `.cursor-plugin/plugin.json` (only `name`
required; a `.cursor-plugin/marketplace.json` exists for the marketplace path) that
can bundle **rules · skills · agents · commands · hooks · MCP servers** — i.e. our
whole adapter in one installable unit, the direct analog of a Claude Code plugin:

```
cookbook-memory-cursor/
├── .cursor-plugin/plugin.json     # { "name": "cookbook-memory", … }
├── mcp.json                       # the recall/remember MCP server
├── hooks/hooks.json               # beforeSubmitPrompt, postToolUse, stop,
│                                   #   sessionStart, preCompact, sessionEnd
├── skills/{recall,remember}/SKILL.md
└── rules/*.mdc
```

Install paths: `--plugin-dir <path>` on the command line (cleanest for the eval
harness), or drop/symlink into `~/.cursor/plugins/local/`. This mirrors our
build-time materialization approach for Claude Code.

> **UNVERIFIED:** whether a plugin-bundled MCP server **auto-approves** in headless
> mode or still hits the §6 approval gate. Test before relying on it.

## 6. The one real gotcha — the MCP approval gate

Configured MCP servers do **not** load automatically: `cursor-agent mcp list` shows
each as `not loaded (needs approval)` (verified locally — every host server reads
`needs approval`). An interactive run prompts; a **headless/autonomous run will get
no memory tools** unless the gate is pre-cleared. Two ways:

- **`cursor-agent mcp enable memory`** before the run — persists `memory` to the
  local approved list (preferred for a sandbox we set up once).
- **`--approve-mcps`** on the run — auto-approves all MCP servers for that
  invocation.

There is a parallel **workspace-trust** gate: a headless run in an untrusted dir
aborts with "Workspace Trust Required" (verified) → pass **`--trust`** (documented
as headless-only) or pre-trust the workspace.

> **Isolation gotcha (verified the hard way):** `cursor-agent mcp enable <id>`
> writes to the **shared approved list under `~/.cursor/cli-config.json`**, and the
> approval **persists** even after the project `.cursor/mcp.json` that defined the
> server is removed (you then `mcp disable` to undo it). So the eval harness must
> **never** run `mcp enable` against the host `~/.cursor` — it would mutate the
> developer's machine (this is exactly what happened during research). Always point
> **`HOME`** at a fresh sandbox dir *first* (that's what relocates `cli-config.json`
> + `mcp.json` + auth — **not** `CURSOR_DATA_DIR`; see §7), then `mcp enable memory`
> (or `--approve-mcps`) inside it. This is the same lesson as the Claude Code
> shared-sandbox work, and it's what makes §9's parallel runs safe.

This is the Cursor analog of Claude Code's MCP-trust / `--strict-mcp-config`
handling and must be wired into the sandbox exactly as we did there (see the
control/plugin tool-parity and shared-sandbox notes in §8).

There are in fact **two distinct MCP gates** for a headless run, and both must be
cleared: (a) **server-load approval** — `mcp enable` / `--approve-mcps` (above);
and (b) **tool-run permission** — the `Mcp(server:tool)` allowlist token below. Tool
permissions live in **`cli-config.json`** (global `~/.cursor/`) / **`cli.json`**
(project `.cursor/`, permissions-only) with `permissions.allow` / `permissions.deny`
tokens — `Shell(…)`, `Read(…)`, `Write(…)`, `WebFetch(domain)`,
`Mcp(server:tool)` (case-insensitive, glob: `memory:*`, `*:recall`, `*:*`; **deny
beats allow**) — and an `approvalMode` (`allowlist` | `unrestricted`).
`-f/--force` / `--yolo` disables prompts entirely. **[docs]** For a remote
(HTTP/SSE) memory server, auth goes in the `mcp.json` entry via
`headers: { "Authorization": "Bearer ${env:TOKEN}" }` (with `${env:VAR}`
interpolation) or an OAuth `auth` block; `cursor-agent mcp login <id>` runs the
interactive OAuth flow — never hardcode secrets in `mcp.json`.

## 7. Headless & isolation (for the test harness)

The headless surface is **the closest of any harness to Claude Code's**, which
makes the eval adapter cheap:

```bash
HOME=/path/to/sandbox \
CURSOR_API_KEY=$KEY \
cursor-agent -p "<task>" \
  --output-format stream-json --trust --approve-mcps \
  --model composer-2.5
```

> **The isolation seam is `HOME`, not `CURSOR_DATA_DIR` — corrected after testing.**
> `CURSOR_DATA_DIR` only relocates the *data* area (`~/.local/share/cursor-agent`:
> transcripts/projects/worker state); it does **not** move `mcp.json`,
> `cli-config.json`, or auth — those resolve from a hardcoded
> `homedir()/.cursor/…`. Verified: with `CURSOR_DATA_DIR=/empty` the run still saw
> the host's MCP servers **and** the host login; with `HOME=/empty` it saw **no MCP
> servers and "Not logged in"**. So **`HOME` (or `HOME/.cursor`) is what gives each
> run its own `mcp.json` + approved-list + auth.** Set `CURSOR_DATA_DIR` *too* if you
> want transcripts captured inside the sandbox as well. See §9 for the parallel-run
> recipe this enables.

- **`stream-json` is close to Claude Code's** (verified — real runs): one JSON
  object per line, types **`system`** (`subtype:"init"`, `cwd`, `model`,
  `session_id`, `permissionMode`, `apiKeySource`), **`user`**, **`assistant`**
  (`message.content[].text`), and a final **`result`** (`subtype`, `is_error`,
  `result`, `session_id`, `request_id`, `duration_ms`, and **`usage`** =
  `inputTokens`/`outputTokens`/`cacheReadTokens`/`cacheWriteTokens`). A tool-using
  turn adds **`tool_call`** (`subtype: started|completed`, with the `mcpToolCall`
  shape from §2) and **`thinking`** (`delta`/`completed`) events — a few cases
  beyond Claude Code's set. → The harness's existing stream-json parser + cost
  accounting port over with small additions; map `usage.*Tokens` to our
  `RetrievedItem.tokens` accounting, and add `tool_call`/`thinking` handling.
- **Output formats:** `text | json | stream-json` (`--output-format`, only with
  `-p`); `--stream-partial-output` for text deltas.
- **Auth (headless) — PLATFORM-DEPENDENT (corrected after end-to-end testing):** the
  credential resolution order is **`auth-token` → `api-key` → `login`**
  (`authSource:r?"auth-token":u?"api-key":"login"`); `CURSOR_AUTH_TOKEN` /
  `CURSOR_API_KEY` env (key from `cursor.com/dashboard`) is the headless path.
  - **Linux (VPS / CI):** credentials are a **file store** (`auth.json`), no
    keychain — so `CURSOR_API_KEY` + an isolated `HOME` "just works" headlessly. This
    is the clean, simple path (and what runs on a VPS today).
  - **macOS:** the binary **unconditionally probes the login keychain at startup**
    (a `security add-generic-password` "cursor-keychain-probe") **even when an API key
    is set** — verified. In an isolated `HOME` that probe hits the host login keychain
    and **hangs with no TTY** ("Keychain operation timed out after 30000ms" /
    "Security process exited 154"). So `CURSOR_API_KEY` *alone* is **not** enough on
    macOS in a sandbox. **Fix (what the adapter does):** provision a **dedicated,
    unlocked, empty login keychain inside the sandbox `HOME`**
    (`security create-keychain` + `unlock-keychain` →
    `<HOME>/Library/Keychains/login.keychain-db`); the probe then writes there with no
    prompt and `CURSOR_API_KEY` authenticates. Verified end-to-end (real `cursor-agent`
    returns `pong` from an isolated sandbox). There is **no `~/.cursor/auth.json`** on
    a normally-logged-in macOS host (login lives in the keychain), so a *copied*
    sandbox still doesn't carry auth — the API key + sandbox keychain is the path.
- **Config-dir isolation:** use **`HOME`** (see the corrected note above), not
  `CURSOR_DATA_DIR`. `HOME=/sandbox` relocates `mcp.json`, `cli-config.json`, and
  auth in one move (the binary reads `homedir()/.cursor/…`). Optionally also set
  `CURSOR_DATA_DIR=/sandbox/data` to capture transcripts in the sandbox. (Neither
  `CURSOR_DATA_DIR` nor `CURSOR_CONFIG_DIR` relocates `mcp.json`/auth — that was the
  earlier mistake.)
- **Per-run store isolation:** `MEMORY_STORE` on the MCP server (fresh path = empty
  store) — the same mechanism as every other harness.
- **Models:** `cursor-agent --list-models` (verified) spans **Anthropic**
  (`claude-opus-4-8-thinking-high`), **OpenAI** (`gpt-5.x-codex…`, `gpt-5.2`), and
  **Cursor** (`composer-2.5`, `auto`). One harness exercising three vendors is a
  strong signal for the project's *model-agnostic* claim.

## 8. Mapping to the eval harness (`eval/memeval/`)

The Claude Code integration lives in `eval/memeval/claudecode/` (`pipeline.py`,
`sandbox.py`, `platform.py`, `memory_server.py`). A Cursor mode is a **sibling
`eval/memeval/cursorcli/`** that reuses the same seams:

| Claude Code piece | Cursor analog | Delta |
|---|---|---|
| `CLAUDE_CONFIG_DIR` sandbox (`sandbox.py`) | **`HOME`** sandbox (relocates `~/.cursor/{mcp.json,cli-config.json,auth}`) | swap the env var; **`HOME` not `CURSOR_DATA_DIR`** (the latter only moves transcripts) |
| interactive `/login` per sandbox (macOS keychain) | **`CURSOR_API_KEY` env** per sandbox | no keychain, no interactive login — the key advantage on macOS |
| `ClaudeRuntime` discovery (`platform.py`) | discover `cursor-agent` on PATH | binary name + `CURSOR_CLI`/`CURSOR_AGENT_CLI` override |
| `claude -p --output-format stream-json` | `cursor-agent -p --output-format stream-json --trust --approve-mcps` | near-identical parser; same `usage` accounting |
| plugin install (build-time materialization) | `--plugin-dir <bundle>` or `~/.cursor/plugins/local/` | a Cursor adapter bundle (`.cursor-plugin/plugin.json` + `mcp.json` + `hooks/`) |
| MCP trust / `--strict-mcp-config` | `cursor-agent mcp enable memory` (one-time in sandbox `HOME`) + `--approve-mcps` | the §6 gate — wire into sandbox setup |
| `--bare` clean baseline | fresh `HOME` + no `.cursor/mcp.json` | no documented `--bare`; isolation via empty `HOME/.cursor` |
| shared sandbox under parallel runs (a constraint today) | **per-stage `HOME`** → fully independent MCP/approval/auth | Cursor parallelizes *more* cleanly than Claude-on-macOS (§9) |

The black-box boundary holds unchanged: the eval engine drives `cursor-agent` as a
user would, points `MEMORY_STORE` at a fresh path, and reads the plugin's events
stream — it never imports the memory engine.

## 9. Parallel-run isolation — why Cursor is *easier* than Claude-on-macOS

The eval pipeline runs several **stages** that must not see each other's state — the
memoryless baseline must have *no* memory MCP, the plugin stages *must*, and
concurrent stages must not collide on a shared approved-list or store. On Claude
Code (macOS) this is constrained: the config dir is isolatable (`CLAUDE_CONFIG_DIR`)
but auth is **keychain-bound**, so a fresh sandbox needs an interactive `/login` and
the dir can't simply be copied — which is why today all pipeline runs share **one**
Claude sandbox.

Cursor removes that constraint, because of the **`CURSOR_API_KEY` env var** (not
because the sandbox is copyable — it isn't; the keychain bites a copied Cursor dir
too). The recipe is **per-stage `HOME` + `CURSOR_API_KEY`**:

```bash
# Each stage gets its own HOME → its own ~/.cursor/{mcp.json, cli-config.json, auth}.
# Baseline: HOME with NO mcp.json (or memory server disabled) → provably no memory.
# Plugin:   HOME with mcp.json + `mcp enable memory` (or --approve-mcps).
# All stages authenticate from the SAME env key — no keychain, no /login, run concurrently.

for stage in base builtin plugin; do
  SANDBOX="$RUN/sandbox-$stage"; mkdir -p "$SANDBOX/.cursor"
  # macOS ONLY: give the sandbox its own login keychain so the startup probe
  # doesn't hang headlessly (no-op on Linux, which uses a file credential store):
  if [ "$(uname)" = "Darwin" ]; then
    mkdir -p "$SANDBOX/Library/Keychains"
    security create-keychain -p "" "$SANDBOX/Library/Keychains/login.keychain-db" 2>/dev/null
    security unlock-keychain -p "" "$SANDBOX/Library/Keychains/login.keychain-db"
  fi
  # write per-stage .cursor/mcp.json (omit for base) ...
  HOME="$SANDBOX" CURSOR_API_KEY="$CURSOR_API_KEY" \
    cursor-agent mcp enable memory          # plugin stages only; writes into THIS HOME
  HOME="$SANDBOX" CURSOR_API_KEY="$CURSOR_API_KEY" CURSOR_DATA_DIR="$SANDBOX/data" \
    cursor-agent -p "<task>" --output-format stream-json --trust --approve-mcps &
done
wait
# (The eval adapter does all of this for you — `memeval.cursorcli.sandbox.build`
#  provisions the macOS keychain; `env_for` sets HOME + CURSOR_API_KEY + CURSOR_DATA_DIR.)
```

| | Claude Code (macOS) | Cursor CLI (Linux VPS/CI) | Cursor CLI (macOS) |
|---|---|---|---|
| Config / MCP isolation | `CLAUDE_CONFIG_DIR` per sandbox ✅ | **`HOME`** per sandbox ✅ | **`HOME`** per sandbox ✅ (`CURSOR_DATA_DIR` not enough) |
| Auth across parallel sandboxes | keychain-bound → interactive `/login`; can't copy | **`CURSOR_API_KEY` env** → file store, no keychain ✅ | `CURSOR_API_KEY` env **+ a per-sandbox login keychain** (the binary still probes the keychain) ✅ |
| Parallel stages (base / builtin / plugin) | shared sandbox today (the current constraint) | **N independent `HOME`s, concurrent** ✅ | **N independent `HOME`s, concurrent** ✅ |
| One-time setup cost | login once | one API key (`cursor.com/dashboard`) | one API key + the adapter auto-provisions a sandbox keychain |

> **macOS keychain step (verified the hard way).** Unlike Linux (pure file store),
> macOS `cursor-agent` probes the login keychain at startup even with an API key, and
> that probe hangs headlessly in an isolated `HOME`. The adapter's sandbox builder
> therefore creates + unlocks an empty `<HOME>/Library/Keychains/login.keychain-db`
> first (a `darwin`-only no-op elsewhere). With that, `CURSOR_API_KEY` + isolated
> `HOME` runs cleanly and in parallel — verified end-to-end. On Linux the user's
> "just the API key works on a VPS" experience holds unchanged.

So the per-run-sandbox model we *wish* we had on Claude-macOS is the **natural,
cheap default** on Cursor: one API key, a fresh `HOME` per stage, fully independent
MCP/approval/auth, run in parallel. The store stays isolated by `MEMORY_STORE` as on
every harness.

## Verdict

Cursor CLI **can host the shared memory core**, and is the **most capable surface of
the four**: same `mcp.json` as Claude Code, a richer hooks system (with a real
`sessionEnd`), a first-class `--plugin-dir` install, a `stream-json` output the
harness can parse with almost no new code, **`HOME`-based config isolation**, and
headless **`CURSOR_API_KEY`** auth that sidesteps the macOS interactive-login pain
and makes per-stage parallel runs cheap (§9). The **only** genuine adapter work
beyond config is clearing the **MCP approval + workspace-trust gates** for
autonomous runs (`mcp enable` / `--approve-mcps` / `--trust`). Designing
the core to the Codex floor still pays off — Cursor's extra hooks become optional
enhancements — and the same model-pulled `recall` retrieval path runs unchanged.
See the [comparison](01-cross-harness-comparison.md) and
[Claude Code](02-claude-code.md) deep-dives.

## Caveats / what I could not verify

**Resolved by empirical test** (no longer open): hooks **do** fire in headless `-p`
(`sessionStart`/`postToolUse`/`sessionEnd` verified); `stop` does **not** fire
headless; `sessionEnd` carries a real `transcript_path` to a `{role, message}`
JSONL. See §4. Still open:

- **`preCompact` in headless** — untested (short run never compacted). Verify before
  relying on pre-compaction extraction.
- **Self-backgrounded dreaming from `sessionEnd`** — does a child process spawned by
  the `sessionEnd` hook survive `cursor-agent` exit? Test before depending on it.
- **Plugin-bundled MCP auto-approval** in headless — UNVERIFIED; may still hit the
  §6 gate even via `--plugin-dir`. (The MCP tool wire-name question is now
  **resolved**: `<server>-<tool>`, e.g. `memory-recall` — see §2.)
- **Isolation env vars — RESOLVED by testing (§7/§9):** `HOME` relocates
  `~/.cursor/{mcp.json,cli-config.json,auth}` (the real isolation seam);
  `CURSOR_DATA_DIR` only relocates the `~/.local/share/cursor-agent` data area
  (transcripts/projects) and does **not** move `mcp.json` or auth; `CURSOR_CONFIG_DIR`
  appears once in the bundle but is not the load-bearing var. Set **`HOME`** for
  config isolation and **`CURSOR_DATA_DIR`** additionally if you want transcripts in
  the sandbox.
- **Versioning / self-update** — `--version` reported **v2026.05.20** but the
  running binary had **auto-updated to v2026.06.24** mid-session (seen in
  `cursor_version` in the hook payload). Cursor self-updates aggressively; pin or
  re-verify `--help`, `mcp`, and hook events against the *actually running* version.
- **Host hygiene note (not ours to fix):** the host `~/.cursor/mcp.json` on this
  machine contains a plaintext GitHub PAT in an MCP server's `env`. Out of scope for
  this doc, but worth flagging to the owner — our sandbox (fresh `HOME`) must
  **not** inherit it.
