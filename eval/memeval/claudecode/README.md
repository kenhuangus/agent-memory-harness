# Run the 5 benchmarks locally through the Claude Code CLI

Every developer can run the five memory benchmarks against the **Claude Code CLI**,
comparing **Claude Code's built-in memory** vs **our memory** (the OKF-backed plugin).

## 1. Install (once)

```bash
cd eval
pip install -e ".[claudecode]"               # memeval + MCP SDK
npm install -g @anthropic-ai/claude-code      # the `claude` CLI (the agent)
python -m memeval.claudecode.run_bench --help # the runner prints the detected CLI
```

`claude` uses your normal Claude Code auth — no separate key needed in the harness.

**Platform support (auto-detected): macOS · Linux · Windows · Windows→WSL.** On
Windows, if `claude` isn't on the native PATH the harness automatically routes
through WSL (`wsl -d <distro> -- claude …`, with paths translated to `/mnt/...`).
Overrides: `CLAUDE_CLI` (native path), `CLAUDE_WSL_DISTRO` (default `Ubuntu`),
`CLAUDE_WSL_PYTHON` (the WSL python that has `memeval`+`mcp`, used by `--mode plugin`).
`off`/`builtin` need only `claude` in WSL; `plugin` also needs the MCP server
importable by that WSL python (`pip install -e ".[claudecode]"` *inside* WSL).

## 2. The three memory modes

| `--mode` | What memory the agent has |
|---|---|
| `off` | none (baseline) |
| `builtin` | **Claude Code's own**: prior sessions written to `CLAUDE.md`, auto-loaded |
| `plugin` | **ours**: an MCP server (`memory_recall`/`memory_remember`) over an OKF store |

## 3. Run

```bash
# one benchmark, our memory, small slice, logged to the shared ledger
python -m memeval.claudecode.run_bench --benchmark longmemeval --mode plugin \
    --model claude-haiku-4-5 --limit 20 --results ../results.json

# the full comparison: all 5 benchmarks x {off, builtin, plugin}
python -m memeval.claudecode.run_bench --benchmark all --mode all \
    --model claude-haiku-4-5 --dev-slice 0.1 --results ../results.json

# offline smoke first (free, no claude): bundled fixtures
python -m memeval.claudecode.run_bench --benchmark longmemeval --mode builtin \
    --path tests/fixtures/longmemeval.json --limit 2 --results /tmp/cc.json
```

Each run logs to `results.json` (cost/budget enforced via `--budget-usd`, default $10)
and appears on the Results page next to the API runs.

## 4. Read the verdict

```bash
python -m memeval.results summary --path ../results.json
```

The config labels distinguish the modes — `claude-code:<model>:builtin` vs
`claude-code:<model>:plugin` — so you can see whether **our memory beats Claude
Code's built-in memory** per benchmark.

## How it works (so you can debug)

- `agent.py` builds a per-task working dir and runs `claude -p <question> --output-format json`.
- **builtin**: writes `CLAUDE.md` (the task's prior sessions) into that dir.
- **plugin**: seeds an OKF store, writes a per-task `.mcp.json` pointing at
  `memeval.claudecode.memory_server`, allows the `memory_*` tools, and reads the
  server's recall log back so recency/relevancy/efficiency are still scored.
- `off`/`builtin` don't expose retrieval, so only **accuracy** is meaningful there;
  `plugin` reports all four metrics.

Plugin details + standalone (non-benchmark) use: [`plugin/README.md`](plugin/README.md).
Notes on what's verifiable without the `claude` binary: the wiring is covered by
offline tests (`pytest -k claudecode`) using an injected fake CLI runner.
