# OpenAI Codex CLI — Memory Extension Surface

> Can Codex host the same memory-plugin core? **Yes**, via MCP tools + the `Stop`
> hook (self-backgrounding the dreaming pass) + `notify`. Codex is the **smallest**
> surface of the three — design the shared core to this floor. Sources:
> `github.com/openai/codex` (`codex-rs/hooks/`, `codex-rs/protocol/src/protocol.rs`),
> `developers.openai.com/codex`, linked issues. Codex evolves fast — verify.

## Capability summary

| Capability | Mechanism | Supported | Notes |
|---|---|---|---|
| Pre-gen injection (per user prompt) | `UserPromptSubmit` hook | Yes | per prompt, not per model call |
| Pre-gen injection (per tool / per model call) | `PreToolUse.additionalContext` | **No** | documented as a no-op ([#19385]) |
| Static project injection | `AGENTS.md` / `codex.md` | Yes | load-time, not dynamic |
| Native tools (`recall`/`remember`) | **MCP** via `[mcp_servers]` in `config.toml`; Codex can also run **as** an MCP server (`codex mcp`) | Yes | model calls them natively |
| Post-tool observation | `PostToolUse` hook | Yes | |
| Post-turn observation | `Stop` hook; `notify` program (`AfterAgent` = turn complete) | Yes | `notify` fires once per turn |
| Lifecycle / dreaming triggers | `Stop`, `PreCompact`/`PostCompact` (`ContextCompacted` event) | Partial | **no true session-end** ([#20603]); **no managed background jobs** |
| Config enablement | `config.toml` (`[mcp_servers]`, `notify`, `[hooks]`), `~/.codex/`, profiles | Yes | |
| Per-run store isolation | env / config (`MEMORY_STORE`) | Yes | fresh path = empty store |
| Headless drive | `codex exec --json` | Yes | drivable from Python |
| Hook count | ~10 events ([#21753]) | — | vs Claude Code's 29+ |

## 1. Pre-generation injection

- **`UserPromptSubmit` hook** — per-prompt context injection (same granularity as
  Claude Code). Use for supplementary top-k push.
- **`AGENTS.md` / `codex.md`** — static, load-time project instructions (the
  AGENTS.md analog). Not dynamic per turn.
- **No per-tool/per-model-call injection:** `PreToolUse.additionalContext` is a
  no-op ([#19385]). → model-pulled `recall` tool is the retrieval path.

## 2. Native tool registration (MCP)

Codex is an MCP **client**. Register the memory server in `config.toml`:

```toml
[mcp_servers.memory]
command = "python3"
args = ["-m", "cookbook_memory.mcp"]
env = { MEMORY_STORE = "/runs/v1.2/store.db" }
```

The model then calls `recall` / `remember`. Codex can **also** expose itself as an
MCP server via `codex mcp` (not needed for our memory use case, but notable for
composing agents).

## 3. Post-tool / post-turn observation

- **`Stop` hook** — fires per turn (turn complete). The primary observation +
  consolidation trigger.
- **`notify` program** — a fire-and-forget subprocess Codex spawns on **one** event
  (`AfterAgent` = turn complete), with a JSON payload as the **last argv argument**:

  ```jsonc
  // argv[-1] to the notify program
  { "type": "...", "thread-id": "...", "turn-id": "...", "cwd": "...",
    "client": "...", "input-messages": [...], "last-assistant-message": "..." }
  ```

  Config:
  ```toml
  notify = ["python3", "/path/to/notify_dream.py"]
  ```
- **`PostToolUse` hook** — observe individual tool calls.

## 4. Lifecycle events for dreaming — the two real constraints

- **No dedicated session-end event.** There is no protocol-level session
  start/end `EventMsg`; a session-exit hook is requested but unshipped ([#20603]).
  The closest signal is the per-turn `Stop` hook. → **Anchor consolidation on
  `Stop` (or `PostCompact`), not session-end.**
- **No managed background-job queue.** Codex won't run a deferred consolidation
  pipeline for you. → The `Stop` hook script (or `notify` program) must
  **self-spawn** the dreaming pass as its own background process. The hook
  `async = true` flag only controls whether Codex *waits* for the hook to return.
- **Compaction is observable:** `ContextCompacted` protocol event +
  `PreCompact`/`PostCompact` hooks; triggered by `/compact` or automatically at
  `model_auto_compact_token_limit`.

## 5. Config enablement, isolation, headless

```toml
# ~/.codex/config.toml (or a profile)
[mcp_servers.memory]
command = "python3"
args = ["-m", "cookbook_memory.mcp"]
env = { MEMORY_STORE = "/runs/v1.2/store.db" }

notify = ["python3", "/path/to/notify_dream.py"]

[hooks]
# Stop / UserPromptSubmit / PreCompact scripts (self-background the dream pass)
```

- **Per-version isolation:** `MEMORY_STORE` per run (fresh path = empty store) —
  the same mechanism as the other harnesses.
- **Headless:** `codex exec --json "<task>"` — drivable from the Python test
  harness, JSON output for grading/logging.

## Verdict

Codex **can host the shared memory core** with three pieces: an **MCP server**
(`recall`/`remember`), the **`Stop` hook with a self-backgrounded** dreaming pass,
and **`PreCompact`** for pre-summary extraction. Its two constraints —
**no session-end** and **no managed background jobs** — make it the **floor** the
shared core must target: model-pulled retrieval, `Stop`+`PreCompact` dreaming
(self-backgrounded), store-by-path isolation. Build to this and the same core runs
unchanged on Claude Code and OpenCode, whose extra hooks become optional adapter
enhancements. See the [comparison](01-cross-harness-comparison.md).

## Caveats

- Hook surface is actively evolving ([#21753], [#20603]); verify against the repo.
- Some third-party blogs cite **fabricated** Codex version numbers / ship dates —
  trust the repo and `developers.openai.com/codex`. Codex's native "Memories"
  feature (`~/.codex/memories/`) is real but is **not** our cross-harness system.
