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
    install.py              #   places the skill into a harness's discovery path (codex/opencode)
  skills/recall/SKILL.md    # the canonical Agent-Skills folder (single source of truth)
  cli.py                    # the `memory-cli` (mcp/install/build-bundle/query/remember/stats/log/reset)
  adapters/claude_code/     # the Claude Code bundle (harness-specific manifests + release build)
    .claude-plugin/         #   plugin.json + marketplace.json
    .mcp.json               #   registers the recall MCP tool (via the launcher)
    hooks/hooks.json        #   lifecycle hooks (fail-open, via the launcher)
    bin/cookbook-memory     #   runtime launcher: find or bootstrap the Python runtime
    mcp_server.py           #   FastMCP recall server → core
    hooks_handler.py        #   single hook entry point → core
    build.py                #   release: materialize skill + manifests → installable bundle
```

**The plugin owns no store, no dreaming, no eval.** A `MemoryClient` builds the memory
**engine** (Brent's `Router` + the store backends, the system diagram's
`route · rank · dedup` node) and calls it; the engine owns routing and all read/write
to Memory. Everything is **fail-open**: if the engine isn't available or errors,
`recall` returns empty — a memory failure never breaks the session (ADR-harness-006).

**Generic by default.** Reusable logic — recall, the events stream, the skill — lives
in `core`. The skill is a single [Agent-Skills](https://agentskills.io) standard
folder (`cookbook_memory/skills/`), authored once. A per-harness **release build**
materializes it into that harness's native bundle so the user gets it through one
native install (ADR-harness-009). Only genuinely harness-specific things (the bundle
manifests, the hook payload parsing, and the release build) live under
`adapters/claude_code/`.

## Conscious surface is recall-only

The conscious agent reads memory via the `recall` MCP tool and never writes. Memory
creation happens asynchronously in the Daydreamer (the dreaming workstream), which
watches the session feed. The `memory-cli remember` command exists for manual/debug
writes by a human.

## Install (Claude Code — standard plugin install)

```bash
claude plugin marketplace add kenhuangus/agent-memory-harness
claude plugin install cookbook-memory@cookbook-memory
```

No clone, no pip. The committed release bundle
([`marketplace/cookbook-memory/`](marketplace/cookbook-memory), ADR-harness-010)
routes the MCP server and every hook through its **runtime launcher**
(`bin/cookbook-memory`, ADR-harness-016), which resolves the Python side in order:

1. `memory-cli` / `memory-hook` already on `$PATH` (or `$COOKBOOK_MEMORY_BIN_DIR`);
2. a managed venv at `~/.cookbook-memory/runtime`, **bootstrapped on first use** from
   this repo's git URL (`uv` when available, else `python3 -m venv` + pip; Python
   ≥ 3.11, macOS/Linux). Hooks never block on the bootstrap — it runs in the
   background, fail-open; `rm -rf ~/.cookbook-memory/runtime` resets it.

Verify with `claude plugin details cookbook-memory` → `Skills (1)`, `Hooks (5)`,
`MCP servers (1)`.

**Prefer a pinned host runtime?** Pre-install the package — the launcher will find it
and never bootstrap. A `pip install --user` from git pulls the plugin *and* its
frozen-contract dependency (`agent-memory-eval[daydream]`, declared as a `git+URL`)
with no clone and no package index:

```bash
pip install --user \
  "cookbook-memory[mcp] @ git+https://github.com/kenhuangus/agent-memory-harness.git#subdirectory=plugin"
```

For local development from this checkout, the repo-level shortcut installs the editable
Python packages into the repo `.venv`, builds an ignored local Claude bundle under
`build/` with commands pinned to that `.venv` (`memory-cli install-claude-plugin`
under the hood), and installs the plugin into the real Claude Code config, explicitly
bypassing the eval sandbox:

```bash
make install-claude-plugin
```

**What lands in the user's repo:** the store at `.cookbook-memory/` scaffolds its own
`.gitignore` on first write (ADR-harness-017) so only the markdown memories
(`markdown/**/*.md`) are committable — databases, locks, the events stream, and dream
state stay per-machine.

**Codex / OpenCode — place the skill into the harness's discovery path:**

```bash
memory-cli install --harness codex      # → .agents/skills/  (also read by OpenCode)
memory-cli install --harness opencode   # → .opencode/skills/
# --scope user installs under your home dir; --link symlinks instead of copying
```

## Develop from source

Working on the plugin itself (editable installs + test deps):

```bash
cd plugin
pip install -e "../eval"          # the eval package (provides the frozen contract)
pip install -e ".[mcp,dev]"       # the plugin + MCP SDK + test deps
```

To regenerate the committed release bundle after changing the skill or manifests
(the test suite fails on drift until you do):

```bash
python -m cookbook_memory build-bundle --out marketplace/cookbook-memory
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

**As a Claude Code plugin:** once installed (see [Install](#install-claude-code--standard-plugin-install)),
the `recall` skill and MCP tool are available in any session, backed by the store at
`${CLAUDE_PROJECT_DIR}/.cookbook-memory`.

## Test

```bash
cd plugin && pytest          # offline, stdlib + pytest; no MCP SDK or network needed
```
