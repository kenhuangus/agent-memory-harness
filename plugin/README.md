# Cookbook Memory — the plugin

Persistent memory for coding agents. A harness-agnostic **core** plus per-harness
**adapters** (Claude Code first). The plugin gives an agent two capabilities —
`recall` (search prior memory) and `remember` (save a memory) — that survive across
turns and sessions.

This is the installable **plugin**, distinct from the eval engine in
[`../eval`](../eval). It depends on the eval package only to reach the frozen
contract (`schema`/`protocols`) via a single seam
([`cookbook_memory/core/contract.py`](cookbook_memory/core/contract.py)); when that
contract becomes its own package (ADR-eval-001), the dependency narrows to it.

## Architecture

```
cookbook_memory/
  core/                     # harness-agnostic — calls the Orchestrator, never a store
    orchestrator.py         #   the route·rank·dedup seam the plugin calls (+ fail-open Null)
    memory.py               #   shared recall/remember logic (events + fail-open)
    events.py               #   the structured memory-events stream (ADR-harness-007)
    config.py               #   store-by-path resolution ($MEMORY_STORE)
    contract.py             #   the ONLY import edge to eval/memeval
  cli.py                    # the `memory` CLI (mcp/query/remember/stats/log/reset)
  adapters/claude_code/     # the Claude Code plugin bundle
    .claude-plugin/         #   plugin.json + marketplace.json
    .mcp.json               #   registers the recall/remember MCP tools
    hooks/hooks.json        #   lifecycle hooks (fail-open no-ops in this iteration)
    skills/{recall,remember}/SKILL.md
    mcp_server.py           #   FastMCP server → core
    hooks_handler.py        #   single hook entry point → core
```

**The plugin owns no store, no dreaming, no eval.** It calls the **Orchestrator**
(the system diagram's `route · rank · dedup` node), which owns routing and all
read/write to Memory. Storage and dreaming are separate workstreams the plugin calls
into. Everything is **fail-open**: if the Orchestrator isn't wired or errors, `recall`
returns empty and `remember` no-ops — a memory failure never breaks the session
(ADR-harness-006).

## Status: walking skeleton (S0–S2)

This iteration delivers the installable surface end-to-end against a **fail-open**
Orchestrator seam — the real route·rank·dedup backend (the storage workstream's
Orchestrator) drops in behind `core/orchestrator.py` with no plugin change. The
Daydreamer (`Stop`/`PreCompact` day pass) and night consolidation are later slices;
the hooks are wired but no-op for now.

## Install

```bash
cd plugin
pip install -e "../eval"          # the eval package (provides the frozen contract)
pip install -e ".[mcp,dev]"       # the plugin + MCP SDK + test deps
```

## Use it

**CLI (human / ops):**

```bash
export MEMORY_STORE=./.cookbook-memory
memory remember "we chose sqlite for the store" --tags decision
memory query "store choice"
memory stats
memory log -n 20
memory reset
```

**As a Claude Code plugin:**

```text
/plugin marketplace add /abs/path/to/plugin/cookbook_memory/adapters/claude_code
/plugin install cookbook-memory
```

Then in any session the `recall` / `remember` MCP tools are available, backed by the
store at `${CLAUDE_PROJECT_DIR}/.cookbook-memory`.

## Test

```bash
cd plugin && pytest          # offline, stdlib + pytest; no MCP SDK or network needed
```
