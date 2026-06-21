# Feature: Claude Code plugin — walking skeleton (S0–S2)

**Status:** Done · **Date:** 2026-06-20

## What this delivers (before → after)

- **Before:** the only Claude Code memory surface was the benchmark-coupled
  `eval/memeval/claudecode/` harness; there was no standalone, installable memory
  plugin, no `memory` CLI, no hooks/skills, and no harness-agnostic core.
- **After:** an installable `cookbook-memory` package with a harness-agnostic **core**
  and a **Claude Code adapter** — `recall`/`remember` as MCP tools, a `memory` CLI,
  the plugin bundle (plugin.json / .mcp.json / hooks / skills), and a structured
  events stream — all routed through the Orchestrator seam and fail-open, so it
  installs and runs in a live session today.

## Requirements & acceptance criteria

1. **AC1 — Package + CLI (S0):** `cookbook-memory` installs; `memory --help` lists
   `mcp`/`query`/`remember`/`stats`/`log`/`reset`; the store/events resolve from
   `$MEMORY_STORE`. *(test_cli, manual `memory --help`)*
2. **AC2 — MCP recall/remember (S1):** the MCP server registers `recall(query,k)` and
   `remember(content,tags)`, both routed through the Orchestrator seam.
   *(test_adapter, live `list_tools()` → `['recall','remember']`)*
3. **AC3 — Plugin bundle (S2):** a valid Claude Code plugin bundle ships — well-formed
   `plugin.json`, `.mcp.json` registering the tools, `hooks.json` wiring the lifecycle
   events, and `recall`/`remember` skills. *(test_adapter bundle-integrity tests)*
4. **AC4 — Plugin calls only the Orchestrator:** the core calls the Orchestrator
   (route·rank·dedup); it never constructs or calls a store, dreaming, or eval. The
   single import edge to `eval/memeval` is `core/contract.py`. *(code review;
   grep: no `memeval` import outside contract.py)*
5. **AC5 — Fail-open (ADR-harness-006):** with no Orchestrator wired, `recall` returns
   empty and `remember` no-ops + logs; nothing raises into the session.
   *(test_core fail-open + NullOrchestrator tests; manual CLI run)*
6. **AC6 — Events stream (ADR-harness-007):** every recall/remember/error/note appends
   one span-friendly JSON line under `$MEMORY_STORE`; `memory log`/`stats` read it.
   *(test_core events tests, test_cli stats/log)*

## Approach

New top-level `plugin/` directory (sibling of `eval/`), its own `pyproject.toml`
(package `cookbook_memory`, console scripts `memory` + `memory-hook`, `[mcp]` extra).

- **`core/`** is harness-agnostic and calls the **Orchestrator** seam only
  (ADR-harness-001/002, ADR-storage-001): `orchestrator.py` (the `Orchestrator`
  protocol + fail-open `NullOrchestrator` + `make_orchestrator` resolver),
  `memory.py` (shared recall/remember + events + fail-open), `events.py` (the events
  stream), `config.py` (store-by-path), `contract.py` (the only `eval/memeval` import).
- **`adapters/claude_code/`** is the thin Claude Code bundle: `mcp_server.py` (FastMCP
  `recall`/`remember` → core), `hooks_handler.py` (one fail-open entry point for all
  lifecycle hooks), and the bundle assets.
- The real Orchestrator (the storage workstream's route·rank·dedup `MemoryFramework`)
  drops in behind `core/orchestrator.py::_build_real_orchestrator` with no other
  plugin change.

**Locked decisions (WHY):**
- *Plugin → Orchestrator only, never a store* — matches the system diagram's
  conscious path; keeps one waist, lets any backend swap in. (ADR-storage-001)
- *Top-level `plugin/`, core + adapters, Claude Code first* — the ADR-harness-001
  target shape; OpenCode/Codex adapters become siblings later.
- *Single `contract.py` import edge to `eval/memeval`* — so the ADR-eval-001 package
  extraction is a one-file move, not a rewrite.
- *Everything fail-open against a Null Orchestrator* — the plugin ships and runs
  before the storage backend lands (the iter-1/iter-2 ramp). (ADR-harness-006)

## Build plan

- [x] Core: contract seam + events stream → AC4, AC6 *(test_core events)*
- [x] Core: Orchestrator seam (protocol + Null + resolver) → AC4, AC5 *(test_core orchestrator)*
- [x] Core: shared recall/remember (fail-open) → AC5 *(test_core fail-open)*
- [x] `memory` CLI (mcp/query/remember/stats/log/reset) → AC1 *(test_cli)*
- [x] Claude Code MCP server (recall/remember tools) → AC2 *(test_adapter + live list_tools)*
- [x] Hooks handler (fail-open no-op) + hooks.json → AC3 *(test_adapter)*
- [x] Plugin bundle (plugin.json/.mcp.json/skills) → AC3 *(test_adapter bundle integrity)*
- [x] pyproject + README

## Quality bars

- **Security / trust boundary:** n/a for this slice — no external model call yet (the
  Daydreamer's redaction boundary, ADR-harness-005, lands with S4a). The CLI is not an
  eval back door; the eval drives the plugin only via `claude` (ADR-eval-001).
- **Non-functional:** the events stream is append-only JSONL, best-effort (an I/O
  error is swallowed). Fail-open is the operability stance: degraded ≫ broken session.
- **Observability:** the events stream (ADR-harness-007) records recall/remember/error/
  note with ids + query, span-friendly for a later Langfuse sink. `memory stats`/`log`
  expose it.
- **Simplicity:** no store, no dreaming, no eval logic in the plugin — only the
  adapter surface + the Orchestrator call.

## Decisions, assumptions & blockers

### Decisions made
- **Console scripts `memory` and `memory-hook`** (not `python -m`): hooks.json and
  .mcp.json invoke names on PATH, robust across the install's interpreter location.
- **`make_orchestrator` returns `NullOrchestrator` whenever the real backend isn't
  constructible** (no `$MEMORY_STORE`, or the storage Orchestrator absent) — one
  fail-open path covers both "not configured" and "not built yet."
- **`note` events for hook fires** so the no-op hooks are still observable in the
  stream (proves the wiring without changing the session).
- **MCP SDK is an optional `[mcp]` extra, lazy-imported**, so the core and CLI install
  and test with zero third-party deps; `memory mcp` prints a clean install hint if it's
  missing rather than a traceback.

### Assumptions
- **Orchestrator interface shape** = `recall(query, *, k, as_of) -> list[Hit]` and
  `remember(content, *, tags, timestamp) -> id`. This is the plugin's expected seam;
  the storage workstream's real Orchestrator must match it (or we adapt at
  `_build_real_orchestrator`). To confirm with Brent.
- **Store env var is `$MEMORY_STORE`** and the per-project store dir is
  `${CLAUDE_PROJECT_DIR}/.cookbook-memory` (matches the cross-harness design).
- **Hook event set** wired = SessionStart / UserPromptSubmit / Stop(async) /
  PreCompact / PostCompact — the points the later slices need; all no-op for now.

### Deferred / blockers
- **Real Orchestrator wiring** — `_build_real_orchestrator` raises until the storage
  workstream exposes its route·rank·dedup Orchestrator; then it's a one-function
  change. Needs Brent's interface. (Not a blocker for this slice — fail-open by design.)
- **Daydreamer (S4a)** + night consolidation (S4b) + live injection (S6) — later
  slices; hooks are stubbed for them now.

### Verification evidence
- `pytest` → **23 passed** (offline, stdlib + pytest; no MCP SDK, no network).
- Installed `memory` CLI: remember/query/stats/log/reset all produce correct JSON;
  events written to `$MEMORY_STORE/events.jsonl`; fail-open returns empty with no crash.
- With the MCP SDK present, the server registers tools `['recall', 'remember']`.
