# Cookbook Memory — the plugin

Persistent memory for coding agents. A harness-agnostic **core** plus per-harness
**adapters**. The plugin gives an agent one in-loop capability — `recall` (search
prior memory) — that survives across turns and sessions. Memory *creation* is handled
asynchronously by the Daydreamer (the dreaming workstream), not by the conscious
agent.

This is the installable **plugin**, distinct from the eval engine in
[`../eval`](../eval). It depends on the eval package only to reach the frozen
contract (`schema`/`protocols`) via a single seam
([`cookbook_memory/core/contract.py`](cookbook_memory/core/contract.py)).

## Architecture

```
cookbook_memory/
  core/                     # harness-agnostic — calls the engine, never a store
    client.py               #   MemoryClient: recall/remember (events + fail-open) over the engine
    events.py               #   the structured memory-events stream (ADR-harness-007)
    config.py               #   store-by-path resolution ($MEMORY_STORE)
    contract.py             #   the ONLY import edge to the engine (Router + stores + contract)
    install.py              #   places skills into a harness's discovery path
  skills/recall/SKILL.md    # the canonical Agent-Skills folder (harness-agnostic)
  cli.py                    # the `memory-cli` (mcp/install/query/remember/stats/log/reset)
  adapters/claude_code/     # the Claude Code bundle (harness-specific manifests only)
    .claude-plugin/         #   plugin.json + marketplace.json
    .mcp.json               #   registers the recall MCP tool
    hooks/hooks.json        #   lifecycle hooks (fail-open)
    mcp_server.py           #   FastMCP recall server → core
    hooks_handler.py        #   single hook entry point → core
```

**The plugin owns no store, no dreaming, no eval.** A `MemoryClient` builds the memory
**engine** (Brent's `Router` + the store backends, the system diagram's
`route · rank · dedup` node) and calls it; the engine owns routing and all read/write
to Memory. Everything is **fail-open**: if the engine isn't available or errors,
`recall` returns empty — a memory failure never breaks the session (ADR-harness-006).

**Generic by default.** Reusable logic — recall, the events stream, skills — lives in
`core`. Skills are a single [Agent-Skills](https://agentskills.io) standard folder
(`skills/`); `memory-cli install` places them into each harness's discovery path
(ADR-harness-009). Only genuinely harness-specific things (the bundle manifests and
the hook payload parsing) live under `adapters/claude_code/`.

## Conscious surface is recall-only

The conscious agent reads memory via the `recall` MCP tool and never writes. Memory
creation happens asynchronously in the Daydreamer (the dreaming workstream), which
watches the session feed. The `memory-cli remember` command exists for manual/debug
writes by a human.

## Install

```bash
cd plugin
pip install -e "../eval"          # the eval package (provides the frozen contract)
pip install -e ".[mcp,dev]"       # the plugin + MCP SDK + test deps
```

## Install the skills into your harness

Skills are one [Agent-Skills](https://agentskills.io) standard folder; place them where
your harness looks for skills:

```bash
memory-cli install --harness claude     # → .claude/skills/
memory-cli install --harness codex      # → .agents/skills/  (also read by OpenCode)
memory-cli install --harness opencode   # → .opencode/skills/
# --scope user installs under your home dir; --link symlinks instead of copying
```

## Use it

**CLI (human / debug):**

```bash
export MEMORY_STORE=./.cookbook-memory
memory-cli remember "we chose sqlite for the store" --tags decision
memory-cli query "store choice"
memory-cli stats
memory-cli log -n 20
memory-cli reset
```

**As a Claude Code plugin:**

```text
/plugin marketplace add /abs/path/to/plugin/cookbook_memory/adapters/claude_code
/plugin install cookbook-memory
```

Then in any session the `recall` MCP tool is available, backed by the store at
`${CLAUDE_PROJECT_DIR}/.cookbook-memory`.

## Test

```bash
cd plugin && pytest          # offline, stdlib + pytest; no MCP SDK or network needed
```
