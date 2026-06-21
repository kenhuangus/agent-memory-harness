# Feature: Claude Code plugin — recall plugin

**Status:** Done · **Date:** 2026-06-21

## What this delivers (before → after)

- **Before:** the only Claude Code memory surface was the benchmark-coupled
  `eval/memeval/claudecode/` harness; there was no standalone, installable memory
  plugin, no `memory-cli`, no hooks/skills, and no harness-agnostic core.
- **After:** an installable `cookbook-memory` package with a harness-agnostic **core**
  and a **Claude Code adapter** — `recall` as an MCP tool, a `memory-cli`, a generic
  `recall` skill, the plugin bundle, and a structured events stream — with `recall`
  wired through Brent's Router to the real store backends.

## Requirements & acceptance criteria

1. **AC1 — Package + CLI:** `cookbook-memory` installs; `memory-cli --help` lists
   `mcp`/`query`/`remember`/`stats`/`log`/`reset`; the store/events resolve from
   `$MEMORY_STORE`. *(test_cli, manual `memory-cli --help`)*
2. **AC2 — recall MCP tool:** the MCP server registers `recall(query,k)` only, routed
   through the `MemoryClient`. *(test_adapter, live `list_tools()` → `['recall']`)*
3. **AC3 — Plugin bundle:** a valid Claude Code plugin bundle ships — well-formed
   `plugin.json`, `.mcp.json` registering `recall`, `hooks.json` wiring the lifecycle
   events, and a `recall` skill. *(test_adapter bundle-integrity tests)*
4. **AC4 — Plugin calls only the engine:** the `MemoryClient` calls the engine (Brent's
   Router + stores); it never constructs or calls a store directly outside that engine.
   The single import edge to the engine is `core/contract.py`. *(code review; grep)*
5. **AC5 — Fail-open (ADR-harness-006):** with no store configured, `recall` returns
   empty; nothing raises into the session. *(test_core fail-open)*
6. **AC6 — Events stream (ADR-harness-007):** every recall/remember/error/note appends
   one span-friendly JSON line under `$MEMORY_STORE`; `memory-cli log`/`stats` read it.
   *(test_core events tests, test_cli stats/log)*
7. **AC7 — Real recall:** with `$MEMORY_STORE` set, `recall` returns real hits routed
   through the Router over the store backends. *(test_core real-recall, test_cli
   round-trip, manual e2e)*

## Approach

Top-level `plugin/` directory (sibling of `eval/`), its own `pyproject.toml`
(package `cookbook_memory`, console scripts `memory-cli` + `memory-hook`, `[mcp]`
extra).

- **`core/`** is harness-agnostic: `client.py` (the `MemoryClient` — recall/remember
  with events + fail-open, plus the private `_Engine` wiring and the `build_engine`
  test seam), `events.py` (the events stream), `config.py` (store-by-path),
  `contract.py` (the only import edge to the engine: Router + stores + contract).
- **`core/skills/`** holds the canonical Agent-Skills folder (`recall`); `core/install.py`
  places it into a harness's discovery path.
- **`adapters/claude_code/`** is the thin Claude Code bundle: `mcp_server.py` (FastMCP
  `recall` → core), `hooks_handler.py` (one fail-open entry point for all lifecycle
  hooks), and the bundle manifests. No skills live here — `memory-cli install` places
  the canonical skill into the harness's discovery path (ADR-harness-009).

**Locked decisions (WHY):**
- *Conscious surface is recall-only* — the model reads memory; all memory creation is
  the Daydreamer's, asynchronously. So the MCP surface is `recall` alone.
- *Plugin → engine only* — matches the system diagram's conscious path; the engine
  (Brent's Router + stores) routes and reads the store. The plugin never holds a store
  directly outside that engine wiring.
- *Generic by default* — reusable logic and skills live in `core`; only the Claude
  bundle manifests and hook parsing are harness-specific.
- *Single `contract.py` import edge* — the contract's source package is swappable by
  editing one file (ADR-eval-001).
- *Fail-open* — no store → empty recall; the plugin never breaks a session
  (ADR-harness-006).

## Build plan

- [x] Core: contract seam + events stream → AC4, AC6
- [x] Core: MemoryClient + engine wiring (build_engine seam) → AC4, AC5, AC7
- [x] Core: shared recall/remember (fail-open) → AC5
- [x] Canonical `recall` skill (core) + `memory-cli install` placement → AC3
- [x] `memory-cli` (mcp/install/query/remember/stats/log/reset) → AC1
- [x] Claude Code MCP server (recall tool) → AC2
- [x] Hooks handler (fail-open) + hooks.json → AC3
- [x] Plugin bundle (plugin.json/.mcp.json) → AC3
- [x] pyproject + README

## Quality bars

- **Security / trust boundary:** n/a for this slice — no external model call yet (the
  Daydreamer's redaction boundary, ADR-harness-005, is the dreaming workstream's). The
  CLI is not an eval back door; the eval drives the plugin only via `claude`
  (ADR-eval-001).
- **Non-functional:** the events stream is append-only JSONL, best-effort. Fail-open is
  the operability stance: degraded ≫ broken session.
- **Observability:** the events stream (ADR-harness-007) records recall/error/note with
  ids + query, span-friendly for a later Langfuse sink. `memory-cli stats`/`log` expose it.
- **Simplicity:** no store, dreaming, or eval logic in the plugin — only the adapter
  surface + the engine (Router) call.

## Decisions, assumptions & blockers

### Decisions made
- **Conscious surface is recall-only.** The MCP server exposes `recall` only; the
  `remember` CLI command remains for manual/debug writes by a human.
- **Console script `memory-cli`** (name-spaced) so the binary does not collide on
  `$PATH`. The hooks entry point is `memory-hook`.
- **Skills are one canonical Agent-Skills folder in `core/skills/`** (ADR-harness-009);
  `memory-cli install --harness <h>` places them into each harness's discovery path
  (`.claude/skills`, `.agents/skills`, `.opencode/skills`). No skills or symlinks in
  the bundle — no duplication, harness-specific code stays minimal.
- **The engine wiring (`_Engine` in `client.py`)** constructs the three backends
  (vectors/markdown/graph) over `$MEMORY_STORE` and routes via Brent's `Router`;
  `recall` maps `RetrievedItem` → `Hit`. `remember` writes to the markdown backend.
- **MCP SDK is an optional `[mcp]` extra, lazy-imported**, so core and CLI install and
  test with zero third-party deps.

### Assumptions
- **Store env var is `$MEMORY_STORE`**; the per-project store dir is
  `${CLAUDE_PROJECT_DIR}/.cookbook-memory`.
- **The headless `claude -p` MCP startup race** is a driver-side concern (the eval
  harness sends a priming turn). A passive stdio server has no lever over it, so the
  plugin does not attempt to mitigate it.

### Deferred / blockers
- **Shared route·rank·dedup Orchestrator (`MemoryFramework`)** — the plugin reaches
  Memory through Brent's `Router` directly via the engine wiring. If a shared
  Orchestrator is later built in the engine package, the engine wiring in `client.py`
  constructs it instead of the Router directly — no other plugin file changes.
- **Daydreamer** (`Stop`/`PreCompact` day pass), night consolidation, and live
  injection — later slices; hooks are wired (fail-open) for them now.

### Verification evidence
- `pytest` → **24 passed** (offline; stdlib + pytest; MCP SDK optional/lazy).
- Installed `memory-cli`: `remember`→`query` round-trips through the Router; memory
  persists to `$MEMORY_STORE` (markdown note + SQLite db + events).
- With the MCP SDK present, the server registers tools `['recall']`.
