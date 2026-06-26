# Cross-Harness Memory Integration — OpenCode vs Claude Code vs Codex

> Can one memory-plugin **core** serve all three popular coding harnesses behind
> thin per-harness **adapters**? This compares OpenCode, Claude Code, and OpenAI
> Codex CLI against the five capabilities our memory framework needs. Owner: **P1
> Keith**.
>
> Sources: OpenCode — our own repo research ([`../opencode/`](../opencode/)).
> Claude Code — official docs (`code.claude.com/docs`, hooks/plugins/mcp/headless,
> current as of 2026-06). Codex — `github.com/openai/codex` (Rust source
> `codex-rs/hooks/`, `codex-rs/protocol/`), `developers.openai.com/codex`, and
> linked issues. Line/issue refs are anchors; verify before building.

## The five capabilities (our design needs)

From [`../opencode/05-integration-strategy.md`](../opencode/05-integration-strategy.md):

1. **Forced pre-generation context injection** — push retrieved memories into the
   model's context before a turn, programmatically.
2. **Native tool registration** — expose `recall` / `remember` as tools the model calls.
3. **Post-tool / post-turn observation** — see tool results + assistant output to
   decide what to remember.
4. **Lifecycle events for dreaming** — a signal to trigger async consolidation.
5. **Config-based enablement + per-run store isolation** — a dev turns it on via
   config; the harness points each run at a fresh, empty store path.

## Capability matrix

> A fourth harness — **Cursor CLI** (`cursor-agent`) — was added later; its column
> is **verified against the installed binary** (v2026.05.20). Full deep-dive:
> [`06-cursor-cli.md`](06-cursor-cli.md).

| Capability | OpenCode | Claude Code | Codex CLI | Cursor CLI |
|---|---|---|---|---|
| **1. Pre-gen injection** | **Per-turn**, every model call: `experimental.chat.system.transform` mutates `system[]` | **Per user-prompt only**: `UserPromptSubmit` → `additionalContext`; `SessionStart` (incl. `compact`). No per-model-call hook. | **Per user-prompt only**: `UserPromptSubmit` hook. No per-tool/per-model-call injection (`PreToolUse.additionalContext` is a no-op, [#19385]). | **Per user-prompt only**: `beforeSubmitPrompt` hook (matcher `UserPromptSubmit`). No per-model-call hook. |
| **2. Tool registration** | Plugin `tool` map **or** MCP | **MCP** (only path), bundled in plugin `.mcp.json` | **MCP** (`[mcp_servers]` in config.toml); Codex can also *be* an MCP server (`codex mcp`) | **MCP** via `mcp.json` (**same schema as Claude Code**); or bundled in a `--plugin-dir` plugin. **Approval gate** (`mcp enable` / `--approve-mcps`) |
| **3. Post-tool / post-turn observation** | `tool.execute.after`, `experimental.text.complete`, `chat.message` | `PostToolUse` (input+output, can add context/modify output), `PostToolBatch`, `Stop`, `PreCompact` | `Stop` hook (per-turn), `notify` program (fires once, `AfterAgent`/turn-complete), `PostToolUse` hook | `postToolUse` (→ `additional_context`, `updated_mcp_tool_output`), `stop`, `afterAgentResponse`/`afterAgentThought` |
| **4. Lifecycle / dreaming trigger** | `event` hook (every EventV2: idle, compaction…) | `SessionStart`, `PreCompact`/`PostCompact`, `Stop`, `SessionEnd` (rich) | `Stop`, `PreCompact`/`PostCompact`. **No true session-end** ([#20603]); **no managed background jobs** — self-background from the `Stop` hook | `sessionStart`, `preCompact`, **`sessionEnd`** (real one; **fires headless**, carries `transcript_path` — verified). `stop` is interactive-only (verified) |
| **5. Config enablement + isolation** | `plugin` / `mcp` in `opencode.json`; store path via env/config | plugin (`.claude-plugin/`) bundling hooks+MCP; `settings.json` `env`; `session_id` per hook; `--bare` for clean test isolation | `config.toml` (`[mcp_servers]`, `notify`, `[hooks]`); `~/.codex/`; profiles; `codex exec --json` headless | `.cursor/` (`mcp.json`, `hooks.json`, `cli.json`) or `~/.cursor/`; **`--plugin-dir`** bundle; **`HOME`** relocates config/MCP/auth (`CURSOR_DATA_DIR` only moves transcripts — verified); **`CURSOR_API_KEY`** keychain-free headless auth → per-stage parallel sandboxes |
| Headless drive (for the harness) | `opencode run --format json` / `serve` + SSE | `claude -p --output-format stream-json` | `codex exec --json` | `cursor-agent -p --output-format stream-json --trust --approve-mcps` (**stream-json ≈ Claude Code's**) |
| Hook count (rough) | ~20 hooks | **29+** hooks | **~10** hooks ([#21753]) | **≈18** hooks |

## What this means: MCP is the universal substrate

**All three are MCP clients.** A single **Python MCP server** exposing
`recall(query, k)`, `remember(content, tags)`, (and optionally `forget`) is the
**portable core** — it works identically in every harness, the model calls it
natively, and it owns the store (so the per-run path / `as_of` / `version`
invariants live in one place we control). This is the load-bearing piece of the
"one core, many adapters" goal.

> Third-party precedent validates this exact pattern: **DREVIHO**
> (`github.com/benediktkraus/dreviho`) does memory via `UserPromptSubmit` (retrieval)
> + `Stop` (storage) across Codex / Claude Code / Gemini CLI; Mem0, Basic Memory,
> and Hindsight integrate via the same hook+MCP surface.

## Where the harnesses diverge: injection + observation

The divergence is exactly where the **adapters** earn their keep:

### Injection granularity (capability 1) — the real difference

- **OpenCode** can force-inject on **every model turn** (`system.transform`).
- **Claude Code & Codex** can only force-inject **per user prompt**
  (`UserPromptSubmit`), not before each internal model call in a tool loop.

**Consequence for a shared core:** do **not** depend on per-model-call forced
injection. Make the **model-pulled `recall` tool the primary retrieval path** (the
model calls it when it needs memory — works everywhere), and use
per-user-prompt injection (`UserPromptSubmit` / `system.transform`) as a
*supplementary* push of top-k memories at turn start. This is the common
denominator that keeps the core uniform. OpenCode's per-turn injection becomes an
*adapter-specific enhancement*, not a core requirement.

### Observation + dreaming triggers (capabilities 3–4)

All three can observe tool results and fire a consolidation pass, but via different
signals:

- **OpenCode:** `tool.execute.after` + `event` (idle/compaction).
- **Claude Code:** `PostToolUse` + `Stop`/`SessionEnd`/`PreCompact` (richest;
  `async: true` hooks for fire-and-forget).
- **Codex:** `Stop` + `notify` + `PreCompact`. **No session-end and no managed
  background jobs** — the `Stop` hook script must **self-spawn** the dreaming pass
  as its own background process.

**Design to the Codex floor.** Codex is the smallest surface (~10 hooks, no
session-end, no background queue). If the shared core anchors dreaming on **`Stop`
/ turn-complete + `PreCompact`** and **self-backgrounds** the consolidation work,
it runs unchanged on all three. Claude Code's `SessionEnd` and OpenCode's `event`
become optional adapter niceties (extra trigger points), not core dependencies.

## Recommended architecture: one core, three adapters

```
                ┌─────────────────────────────────────────────┐
                │       Memory Core (portable, Python)         │
                │  persistence · router · retrieval · dreaming │
                │  store keyed by MEMORY_STORE path (per run)  │
                │  surfaces:  `memory mcp` (model) · CLI (us)  │
                └───────────────┬─────────────────────────────┘
                                │ MCP (model, in-loop) + `memory` CLI (harness/human)
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
┌───────▼────────┐    ┌─────────▼─────────┐   ┌─────────▼─────────┐
│ OpenCode adapter│    │ Claude Code adapter│   │  Codex adapter    │
│ plugin: tools + │    │ plugin: .mcp.json +│   │ config.toml:      │
│ system.transform│    │ hooks.json         │   │ [mcp_servers] +   │
│ + event         │    │ (PostToolUse, Stop,│   │ [hooks]/notify    │
│                 │    │  PreCompact, …)    │   │ (Stop, PreCompact)│
└─────────────────┘    └────────────────────┘   └───────────────────┘
```

- **Core (write once):** the MCP server (`recall`/`remember`) + the store +
  router + dreaming. It is harness-agnostic. The store location is an env/config
  value (`MEMORY_STORE`), giving us per-version isolation for free in every
  harness.
- **Adapter = config + a few hook scripts per harness:**
  - **OpenCode adapter:** an OpenCode plugin — registers the tools (or points at
    the MCP server), adds `system.transform` injection and `event`-driven
    dreaming. (Already our plan in [`../opencode/05`](../opencode/05-integration-strategy.md).)
  - **Claude Code adapter:** a Claude Code plugin (`.claude-plugin/plugin.json`)
    bundling `.mcp.json` (the same MCP server) + `hooks/hooks.json`
    (`UserPromptSubmit` retrieval, `PostToolUse`/`Stop` observation,
    `PreCompact`/`SessionEnd` dreaming, `async: true`).
  - **Codex adapter:** `config.toml` `[mcp_servers]` (same MCP server) + `[hooks]`
    / `notify` scripts (`Stop` → self-backgrounded dreaming, `PreCompact`).

Each adapter is **thin**: config plus small shell/TS shims that call the core's
MCP server or a tiny local endpoint. The retrieval+dreaming logic lives once.

## Core packaging: one binary, two surfaces (MCP **and** CLI)

> Resolves two PR questions: "CLI or MCP?" and "what language?"

**MCP and a CLI are not alternatives — they serve different consumers, and we want
both from one core program:**

| Consumer | Surface | Why |
|---|---|---|
| **The model, in-loop** | **MCP tool** (`recall` / `remember`) | native, typed, discoverable in the tool list, no shell hop, gated by the model's own decision — this is "memory as a native capability" (constraint #1) |
| **The dreaming worker / a human dev** | **CLI** (`memory dream`, `memory query`, `memory reset --store …`, `memory stats`) | scripting, cron, ops, debugging, per-run store setup |

A model can only reach a CLI by shelling out through the harness's `bash`/`shell`
tool — strictly worse for in-loop use (consumes the shell tool, no schema, bash
permission prompts, model must *know* to shell out). So the **CLI is for us and the
human; MCP is for the model.** One core exposes both, mirroring Codex's own split
(`codex mcp` vs `codex exec`):

```
memory                       # the single core program (Python)
  memory mcp                 # speak MCP over stdio  → the model's recall/remember
  memory dream [--store P]   # CLI → run a consolidation pass (harness uses this between batches)
  memory query "<q>"         # CLI → debug retrieval
  memory reset --store P     # CLI → fresh per-run store (per-version isolation)
  memory stats --store P     # CLI → inspect memory state
```

This also means the **test harness never needs special access to the agent**: it
drives the agent as a user would (MCP does the in-loop memory). The `memory` CLI
exists for the human dev and for the system's *own* hook scripts to call (e.g. the
`Stop`-fired dreaming pass); it is **not** a back door for the eval harness.

> **Black-box boundary (revised here).** The eval engine treats the memory system
> as a **black box**: it drives the coding harness with the plugin installed
> (`claude -p`) and points each run at a fresh `$MEMORY_STORE`, then verifies
> memory behavior by reading the plugin's **events stream** output — it does **not**
> import the engine and does **not** call the `memory` CLI to set up or trigger
> dreaming. Dreaming is triggered by the plugin's own hooks inside the run, exactly
> as it is for a human. The store path is the only seam. (See
> [`05-plugin-mvp-plan.md`](05-plugin-mvp-plan.md) ADR-P1/P11; this supersedes the
> earlier framing where the harness called the CLI for dreaming.)

## Core language: **Python** (reuse the engine)

The memory engine (Brent's `stores/` + `router.py`, Scott's `dreaming/`) is already
**Python**. The core is therefore **Python**:

- **No porting, no duplicated logic** — the MCP server and CLI are thin wrappers
  that `import` the existing engine. Critical for the 2-week sprint. (The engine
  lives in the **memory system's own package**, extracted from `eval/memeval/` so
  the eval engine never imports it — see [`05`](05-plugin-mvp-plan.md) ADR-P1. The
  eval harness, also Python, shares no code with the core; it only drives the
  harness binary.)
- MCP via the official Python SDK (`mcp` / FastMCP); CLI via Typer/argparse.
- Ships via `pipx` / `uvx` (a dev installs `cookbook-memory`, then points each
  harness's config at `memory mcp`).
- **Cost accepted:** a Python runtime dependency for any dev installing the plugin.
  Acceptable for a research contribution; **Go (single static binary)** or
  **TypeScript (native adapter glue)** are documented as a *post-project rewrite*
  if the plugin gains real traction — both would require porting the engine and
  duplicating logic the eval harness already owns, so they are out of scope now.

Adapter glue stays in each harness's native language (TS plugin for OpenCode /
Claude Code; `config.toml` for Codex), but it only does config + small shims to
`memory mcp` / the `memory` CLI — the logic stays in the Python core.

## Constraints to bake into the core (so adapters stay thin)

1. **Retrieval primary = model-pulled `recall` tool**, not forced injection — the
   only mechanism uniform across all three.
2. **Dreaming trigger = turn-complete (`Stop`) + pre-compaction**, self-backgrounded
   — the Codex floor; richer triggers are adapter add-ons.
3. **No reliance on a true session-end signal** (Codex lacks it).
4. **Store path via `MEMORY_STORE` env/config** — one isolation mechanism for all
   three and for per-version test runs.
5. **Token accounting our way** — each harness counts tokens differently (OpenCode
   is char/4); the core reports `RetrievedItem.tokens` so the efficiency metric is
   consistent across harnesses.
6. **One Python core, two surfaces** — `memory mcp` (the model's in-loop tool) and a
   `memory` CLI (harness/human: dreaming, reset, query). The engine logic lives once,
   in Python, reused from the eval harness — no port, no duplication.

## Verdict

**Yes — a single memory core can serve all three harnesses behind thin adapters**,
because the model-callable **MCP tool** (`recall`/`remember`) is supported
identically by all three and carries the heavy logic. The adapters differ only in
(a) *supplementary* forced injection (full per-turn in OpenCode; per-prompt in
Claude Code / Codex) and (b) the *signal* that triggers dreaming
(`event` / `SessionEnd` / `Stop`+self-background). Designing the core to the
**Codex floor** — model-pulled retrieval, `Stop`+`PreCompact` dreaming, store-by-path
isolation — keeps it uniform, with each harness's richer hooks layered in as
optional adapter enhancements. This makes the memory framework a genuine,
distributable contribution beyond this project, not a one-harness artifact.

## Per-harness deep-dives

- OpenCode (full): [`../opencode/`](../opencode/) — esp.
  [`02-extension-surfaces.md`](../opencode/02-extension-surfaces.md) and
  [`05-integration-strategy.md`](../opencode/05-integration-strategy.md).
- Claude Code: [`02-claude-code.md`](02-claude-code.md).
- Codex: [`03-codex.md`](03-codex.md).

## Caveats

- Hook names/counts and issue references reflect research at this date and move
  fast (Codex especially is evolving its hook surface — [#21753], [#20603]).
- Some third-party blog sources gave **fabricated** Codex version numbers and ship
  dates; treat repo + official developer docs as authoritative. The native Codex
  "Memories" feature (`~/.codex/memories/`) is real but is **not** our system —
  ours is the cross-harness framework.
