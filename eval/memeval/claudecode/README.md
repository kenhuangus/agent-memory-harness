# Run the 5 benchmarks locally through the Claude Code CLI

Every developer can run the five memory benchmarks against the **Claude Code CLI**
on their own machine, comparing **Claude Code's built-in memory** vs **our memory**
(the OKF-backed plugin). No API key is used — the runs go through your existing
**Claude Code subscription** (see [Auth](#auth)).

- [1. Install (once)](#1-install-once)
- [2. Auth — subscription only, no API key](#auth)
- [3. The memory modes](#3-the-memory-modes)
- [4. Quickstart](#4-quickstart)
- [5. Run each benchmark](#5-run-each-benchmark)
- [6. How many entries (floors + group-aware draw)](#6-how-many-entries)
- [7. Raw per-run artifacts](#7-raw-per-run-artifacts)
- [8. Read the verdict](#8-read-the-verdict)
- [9. How it works (debugging)](#9-how-it-works)

## 1. Install (once)

```bash
cd eval
pip install -e ".[claudecode]"                # memeval + MCP SDK (the plugin server)
pip install -e ".[hf]"                         # datasets — needed to pull the REAL benchmark data
npm install -g @anthropic-ai/claude-code       # the `claude` CLI (the agent under test)
python -m memeval.claudecode.run_bench --help  # prints the detected CLI + auth banner
```

The CODE benchmarks (`swe_contextbench`, `swe_bench_cl`, `contextbench`) only
score **accuracy** when the SWE-bench Docker grader is available — add
`pip install -e ".[swebench]"` and a running Docker daemon (Linux/WSL). Without
it they still run end-to-end and report the memory metrics (recency / relevancy
/ efficiency); accuracy shows `0.00`. See [`../PROTOCOL.md`](../PROTOCOL.md) for
wiring the grader.

**Platform support (auto-detected): macOS · Linux · Windows · Windows→WSL.** On
Windows, if `claude` isn't on the native PATH the harness routes through WSL
(`wsl -d <distro> -- claude …`, paths translated to `/mnt/...`). Overrides:
`CLAUDE_CLI` (native path), `CLAUDE_WSL_DISTRO` (default `Ubuntu`),
`CLAUDE_WSL_PYTHON` (the WSL python that has `memeval`+`mcp`, used by
`--mode plugin`). `builtin` needs only `claude`; `plugin` also needs the MCP
server importable by that python (`pip install -e ".[claudecode]"` *inside* WSL).

## Auth

**Subscription only — no API key.** The harness strips `ANTHROPIC_API_KEY` and
`ANTHROPIC_AUTH_TOKEN` from every `claude` invocation, so runs always use your
Claude Code OAuth login and never incur API billing. Log in once with `claude`
(interactively) and you're set. The runner prints a banner attesting to this on
every run. The `$…` cost column is **nominal accounting** (token count × the
price table), not a charge.

## 3. The memory modes

| `--mode` | What memory the agent has |
|---|---|
| `builtin` | **Claude Code's own**: the task's prior sessions are written to `CLAUDE.md` and auto-loaded |
| `plugin` | **ours**: an MCP server (`memory_recall` / `memory_remember`) over an OKF store |
| `off` | none (baseline) — accepted explicitly, but **not** part of `--mode all` |

`--mode all` runs the head-to-head that matters: **builtin vs plugin**.

## 4. Quickstart

```bash
cd eval

# Offline smoke first (free, no claude, bundled fixtures) — proves the wiring:
python -m memeval.claudecode.run_bench --benchmark longmemeval --mode builtin \
    --path fixtures --limit 2 --results /tmp/cc.json

# The full comparison: all 5 benchmarks × {builtin, plugin}, real data,
# per-benchmark entry floors, $200 cap, raw artifacts written under runs/:
python -m memeval.claudecode.run_bench --benchmark all --mode all \
    --model claude-haiku-4-5 --out-dir runs/claudecode --results runs/claudecode/results.json
```

## 5. Run each benchmark

Run from the `eval/` directory. Each command below runs **one** benchmark with
both memory modes (`--mode all` = builtin + plugin). Drop `--mode all` for a
single mode (e.g. `--mode plugin`). Entry counts default to each benchmark's
[long-memory floor](#6-how-many-entries); override with `--limit N`.

**MemoryAgentBench** — QA; one long shared context, many questions (accurate
retrieval, test-time learning, conflict resolution). Real source `ai-hyz/MemoryAgentBench`.
```bash
python -m memeval.claudecode.run_bench --benchmark memoryagentbench --mode all \
    --model claude-haiku-4-5 --out-dir runs/claudecode --results runs/claudecode/results.json
```

**LongMemEval** — QA; each question carries ~50 timestamped sessions (temporal
reasoning, knowledge updates, abstention). Real source `xiaowu0162/LongMemEval`.
```bash
python -m memeval.claudecode.run_bench --benchmark longmemeval --mode all \
    --model claude-haiku-4-5 --out-dir runs/claudecode --results runs/claudecode/results.json
```

**SWE-ContextBench** — CODE; in-task + cross-task context retrieval, grouped by
shared-context links. Real source `jiayuanz3/SWEContextBench`. Memory lives
*across* entries, so this defaults to the **group-aware** draw.
```bash
python -m memeval.claudecode.run_bench --benchmark swe_contextbench --mode all \
    --model claude-haiku-4-5 --out-dir runs/claudecode --results runs/claudecode/results.json
```

**SWE-Bench-CL** — CODE; chronological per-repo issue *sequences* (continual
learning); memory = prior issues in the sequence. Real source
`thomasjoshi/agents-never-forget`. Defaults to the **group-aware** draw.
```bash
python -m memeval.claudecode.run_bench --benchmark swe_bench_cl --mode all \
    --model claude-haiku-4-5 --out-dir runs/claudecode --results runs/claudecode/results.json
```

**ContextBench** — CODE; in-task gold-context span retrieval. Real source
`Contextbench/ContextBench`.
```bash
python -m memeval.claudecode.run_bench --benchmark contextbench --mode all \
    --model claude-haiku-4-5 --out-dir runs/claudecode --results runs/claudecode/results.json
```

To run any benchmark against the tiny **bundled fixtures** instead of the real
dataset (fast, no download), add `--path fixtures`.

## 6. How many entries

Every run **reports** the dataset entries it used, both on the console
(`entries=33/273 (limit=33,group)`) and in the record JSON
(`entries_used` / `entries_available` / `limit` / `selection`).

`--limit` controls the count. With no `--limit`, each benchmark uses a
**long-memory floor** tuned to its real structure (these are *minimums* so a
bare run is a meaningful memory test, not a single-entry run):

| Benchmark | Floor | Draw | Why |
|---|---:|---|---|
| `memoryagentbench` | 20 | flat | 1 long-context session/entry — 20 questions |
| `longmemeval` | 20 | flat | ~50 sessions/entry — 20 questions is deep + broad |
| `swe_bench_cl` | 33 | group | covers ~1 full repo sequence so priors accumulate |
| `swe_contextbench` | 50 | group | singleton-heavy; draw whole large groups for memory |
| `contextbench` | 20 | flat | in-task span retrieval (not cross-session) |

- `--limit N` — use N entries (overrides the floor for every benchmark in the run).
- `--limit 0` — the **whole** dataset (no cap). Use the [`$200` budget](#auth)
  guard to bound cost.
- `--select auto|flat|group` — how the limited sample is drawn. `flat` = first-N;
  `group` = whole `group_id` groups, **largest first** (so a continual-learning
  bench doesn't sample entries that have no priors); `auto` (default) = `group`
  for `swe_bench_cl` / `swe_contextbench`, `flat` otherwise.

Other knobs: `--model` (default `claude-haiku-4-5`), `--k` (retrieval depth,
default 5), `--timeout` (per-task seconds, default 600), `--budget-usd` (hard cap,
default **$200**; `<=0` = no cap / pure accounting).

## 7. Raw per-run artifacts

Pass `--out-dir DIR` to write everything a run produced, so results are
inspectable and reproducible:

```
DIR/
  <benchmark>__<mode>.record.json       # the ledger row: metrics, entries_used/available/limit, cost
  <benchmark>__<mode>.trajectory.jsonl  # one JSON line per task (retrieve/generate/write steps)
  <mode>/<task_id>/                      # the agent's working dir for that task:
      CLAUDE.md      (builtin) | .mcp.json + memory/ + recall.jsonl  (plugin)
```

Without `--out-dir`, only the aggregate ledger (`--results`) is written and
per-task working dirs go to a temp location.

## 8. Read the verdict

```bash
python -m memeval.results summary --path runs/claudecode/results.json   # hypothesis scoreboard
python -m memeval.results show    --path runs/claudecode/results.json   # per-run lines incl. entries=
```

Config labels distinguish the modes — `claude-code:<model>:builtin` vs
`claude-code:<model>:plugin` — so you can see whether **our memory beats Claude
Code's built-in memory** per benchmark.

## 9. How it works

- `agent.py` builds a per-task working dir and runs `claude -p <question> --output-format json`.
- **builtin**: writes `CLAUDE.md` (the task's prior sessions) into that dir.
- **plugin**: seeds an OKF store, writes a per-task `.mcp.json` pointing at
  `memeval.claudecode.memory_server`, allows the `memory_*` tools, and reads the
  server's recall log back so recency / relevancy / efficiency are still scored.
- `off` / `builtin` don't expose retrieval, so only **accuracy** is meaningful
  there; `plugin` reports all four metrics.

Plugin details + standalone (non-benchmark) use: [`plugin/README.md`](plugin/README.md).
Everything except the live `claude` call is covered by offline tests
(`python tests/test_smoke.py`, or `pytest -k claudecode`) using an injected fake
CLI runner — so you can verify the wiring with nothing installed.
