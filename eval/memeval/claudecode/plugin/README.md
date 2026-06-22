# memeval-memory — Cookbook Memory plugin for Claude Code

> **DEPRECATED** — this tree is the original eval-bundled plugin shape and is
> no longer the canonical install. The shipping plugin lives under
> `plugin/cookbook_memory/` (Python package) and `plugin/marketplace/cookbook-memory/`
> (marketplace install per PR #70). New collaborators should install from there.
> This README + sibling files will be deleted after the first green
> `memeval-bench --mode plugin-real memoryagentbench` run from the migrated
> hook-handler path; until then they remain as historical reference for the
> MCP-tool-based v1 design.

Gives Claude Code **persistent, OKF-backed memory** via two MCP tools:

- `memory_recall(query, k)` — search prior notes (ranked).
- `memory_remember(content, tags)` — save a new note.

Memory is stored as a portable **OKF bundle** under `${CLAUDE_PROJECT_DIR}/.memeval-memory`
(readable by any OKF consumer; see `docs/okf/`).

## Prerequisites

```bash
pip install -e "eval[claudecode]"     # installs memeval + the MCP SDK
npm install -g @anthropic-ai/claude-code   # the Claude Code CLI
```

## Use it (two ways)

**A. Quick — point a session at the MCP server directly:**

```bash
claude --mcp-config eval/memeval/claudecode/plugin/.mcp.json \
       --allowedTools mcp__memeval-memory__memory_recall,mcp__memeval-memory__memory_remember
```

**B. Install as a plugin (persists across sessions):**

```text
/plugin marketplace add /abs/path/to/eval/memeval/claudecode/plugin
/plugin install memeval-memory
```

Then in any session: "recall what we decided about X", or "remember: …". The agent
calls the tools; memory survives across sessions in the OKF bundle.

> The benchmark runner (`python -m memeval.claudecode.run_bench --mode plugin`)
> wires the same server up automatically per task — you do **not** need to install
> the plugin to run the benchmarks.
