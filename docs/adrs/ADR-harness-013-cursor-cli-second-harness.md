---
id: ADR-harness-013
domain: harness
title: Add the Cursor CLI as a second eval harness backend (sibling adapter, shared agent core)
status: Proposed
date: 2026-06-26
contract: false
supersedes: none
superseded_by: none
owner: Keith (P1)
origin: docs/harnesses/06-cursor-cli.md
---

# ADR-harness-013: Add the Cursor CLI as a second eval harness backend

**Status:** Proposed · **Date:** 2026-06-26 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
The project's thesis is a **model-agnostic, harness-agnostic** memory framework:
one portable core (the MCP `recall`/`remember` server + the Daydreamer), thin
per-harness adapters (see
[`docs/harnesses/01-cross-harness-comparison.md`](../harnesses/01-cross-harness-comparison.md)).
Today only **one** harness is wired into the eval pipeline — Claude Code
(`eval/memeval/claudecode/`). That makes "harness-agnostic" an unproven claim. The
**Cursor CLI** (`cursor-agent`) was researched
([`docs/harnesses/06-cursor-cli.md`](../harnesses/06-cursor-cli.md)) and verified
against the installed binary to be the *richest* surface of the four: same
`mcp.json` schema as Claude Code, a `stream-json` output nearly byte-compatible with
Claude Code's, a first-class `--plugin-dir` install, a full hooks system, and — per
[`ADR-harness-014`](ADR-harness-014-cursor-home-isolation-api-key-auth.md) — a
*better* parallel-isolation story on macOS. Adding it turns "harness-agnostic" into a
demonstrated property and lets one pipeline exercise three model vendors (Anthropic /
OpenAI / Cursor) through one harness.

The question this ADR settles is **how invasive** the second backend is — whether it
reaches into the existing Claude Code agent, or lands as an isolated sibling.

## Options considered
- **Sibling adapter package `eval/memeval/cursorcli/`**, mirroring the shape of
  `claudecode/` (a `cli.py` runner → a `CursorResult`, a `platform.py` discovery, a
  `sandbox.py` isolation helper, a `CursorCodeAgent` satisfying the existing
  `AgentAdapter`), reusing the shared `run_agent` / grading / trajectory / cost
  machinery. The two harnesses share **no** harness-specific code; they share only
  the harness-agnostic seams (`AgentAdapter`, `run_agent`, the graders, `CostTracker`).
- **Generalize `claudecode/` into a parametric multi-harness module.** Rejected for
  now: `claudecode/agent.py` is ~1700 lines of Claude-specific behavior (WSL routing,
  the MCP startup-race priming turn, VISTA gold-seeding, the daydream-drain barrier,
  `plugin-real` native install). Forcing Cursor through a shared abstraction would
  couple two fast-moving CLIs and risk regressing the working Claude path. A premature
  abstraction over two examples is the wrong abstraction.
- **A thin `--cli` flag inside `claudecode/`** that swaps the binary. Rejected:
  the binaries differ in flags (`--trust`, `--approve-mcps`, `--plugin-dir`), auth
  (`CURSOR_API_KEY` vs subscription/keychain), isolation env var (`HOME` vs
  `CLAUDE_CONFIG_DIR`), MCP approval gate, and stream-json event shapes. "Swap the
  binary" hides real divergence and would litter the Claude path with `if cursor:`.

## Decision
Add Cursor as a **sibling adapter package `eval/memeval/cursorcli/`** that satisfies
the existing harness-agnostic `AgentAdapter` seam and reuses `run_agent`, the
graders, and the cost/trajectory machinery unchanged. The pipeline and bench runner
select the backend with a new **`--harness {claude,cursor}`** option (default
`claude`, so every existing invocation is byte-identical). The two harness packages
share no harness-specific code.

## Rationale
The `AgentAdapter` boundary already exists precisely so a new agent plugs in with one
`solve(task, ctx)` method while cost/grading/trajectory stay centralized
(`eval/memeval/agent.py`). A sibling package is the lowest-risk way to prove
harness-agnosticism: it cannot regress the Claude path (no shared mutable code), it
mirrors a structure the team already understands, and it keeps each adapter free to
track its own fast-moving CLI. We can extract a common base **later, from two real
implementations** — the honest time to abstract.

## Tradeoffs & risks
- **Some duplication** between `claudecode/cli.py` and `cursorcli/cli.py` (argv
  build, stream-json parse, progress monitor). Accepted: the parsers diverge in event
  shape (Cursor adds `tool_call`/`thinking`), and a shared parser would need a
  per-harness dialect anyway. We keep the duplication small and documented, and note
  it as the seam to extract if a **third** harness lands (rule of three).
- **Two CLIs to keep current.** Cursor self-updates aggressively (observed
  2026.05.20 → 2026.06.24 mid-session). Mitigation: the adapter pins nothing it
  doesn't have to and the doc records the verify-against-running-version rule.
- **Feature parity is partial at first.** The Cursor adapter targets the core modes
  (`off` / `builtin` / `plugin-real`); the Claude-specific niceties (VISTA gold-seed,
  daydream-drain barrier, primed MCP turn) are ported only as needed and tracked as
  follow-ups, not blockers.

## Consequences for the build
- **Policy — package layout:** `eval/memeval/cursorcli/` mirrors `claudecode/`:
  `platform.py` (discover `cursor-agent`; `CURSOR_CLI`/`CURSOR_AGENT_CLI` override),
  `cli.py` (`run_cursor` → `CursorResult`, stream-json parse + progress monitor),
  `sandbox.py` (HOME-based isolation + `CURSOR_API_KEY` auth — see
  [`ADR-harness-014`](ADR-harness-014-cursor-home-isolation-api-key-auth.md)),
  `agent.py` (`CursorCodeAgent(AgentAdapter)`), `__init__.py`.
- **Policy — selection seam:** a `--harness {claude,cursor}` flag on
  `memeval-pipeline` and `memeval-bench`; default `claude`. The pipeline's
  `_make_agent` dispatches on it. No existing default changes.
- **Policy — shared core only:** the Cursor adapter imports from
  `memeval.agent` / `memeval.schema` / `memeval.cost` / the graders, and from its own
  `cursorcli.*` — **never** from `memeval.claudecode.*`. The black-box boundary
  (ADR-eval-001/003) holds: the adapter drives `cursor-agent` as a user would, points
  `MEMORY_STORE` at a fresh path, and reads the plugin's events stream; it never
  imports the memory engine.
- **Policy — selection is a run option, not a separate entrypoint:** the harness is
  chosen by the `--harness` flag on the existing `memeval-pipeline` / `memeval-bench`
  entrypoints, exactly like every other run option — e.g.
  `make pipeline ARGS="--harness cursor …"`. Do NOT add a parallel `pipeline-cursor`
  target/command; one entrypoint, one new option. Plus an `.env.example` entry for
  `CURSOR_API_KEY`. Cursor adapter tests run offline via an injected fake runner
  (mirroring the Claude offline tests), so CI needs no Cursor binary or key.
- **Cross-link:** isolation/auth in
  [`ADR-harness-014`](ADR-harness-014-cursor-home-isolation-api-key-auth.md); MCP
  wiring + approval gate + stream-json parsing in
  [`ADR-harness-015`](ADR-harness-015-cursor-mcp-wiring-approval-gate.md).
