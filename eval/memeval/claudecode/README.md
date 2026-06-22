# Run the 5 benchmarks locally through the Claude Code CLI

Every developer can run the five memory benchmarks against the **Claude Code CLI**
on their own machine, comparing **Claude Code's built-in memory** vs **our memory**
(the OKF-backed plugin). No API key is used — the runs go through your existing
**Claude Code subscription** (see [Auth](#auth)).

- [1. Install (once)](#1-install-once)
- [2. Auth — subscription only, no API key](#auth)
- [2a. Sandboxed config (isolate from your host `~/.claude`)](#sandboxed-config)
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
memeval-bench --help                           # prints the detected CLI + auth banner
memeval-bench --list-benchmarks                # the five ids you can run on their own
```

Installing puts a **`memeval-bench`** command on your PATH — the short form of
`python -m memeval.claudecode.run_bench`. The two are interchangeable; use the
module form if you're running from a checkout without installing. Every example
below works with either.

CODE tasks run as a **real coding agent** by default (`--code-mode agentic`):
`claude` works in a fresh checkout of the repo, edits the source files with its
native tools, runs the tests, and the harness captures `git diff` as the
prediction. (`--code-mode blind` keeps the old one-turn "emit a diff" behavior.)

Grading is **Docker-free** (`--grader`, default `auto`):

* `swe_contextbench` / `swe_bench_cl` → **local test execution** (`LocalExecGrader`):
  a fresh checkout + the gold `test_patch` applied by the harness + the project's
  tests run in a per-task venv. Best-effort and host-dependent; it reports `None`
  (UNGRADED — excluded from accuracy) whenever the env can't be built, never a
  fake miss. NOT comparable to a containerized SWE-bench leaderboard.
* `contextbench` → **retrieval-only**: scored by its native recall/precision/F1
  over gold spans (no test execution), so it uses no grader.

No extra install and no Docker daemon are needed. See
[`../PROTOCOL.md`](../PROTOCOL.md) §5 and
[`../../docs/adrs/ADR-eval-002-docker-free-code-grading.md`](../../../docs/adrs/ADR-eval-002-docker-free-code-grading.md).

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

## Sandboxed config

By default `claude` reads the *host* user's `~/.claude` — global `CLAUDE.md`,
every installed skill and agent, `settings.json`, MCP servers. For a clean
benchmark you usually want the opposite: a `claude` that sees **only** what the
harness hands it (the memory plugin via `--mcp-config`) and **nothing** of the
host. That removes a confound — your personal skills/agents can't help or skew
the agent under test — and makes runs reproducible across machines.

The harness supports this with a **project-local sandbox config dir**
(`eval/.claude-sandbox/`, gitignored). When it exists, every `claude` invocation
runs with `CLAUDE_CONFIG_DIR` pointed at it, so the CLI discovers no host skills,
agents, or `CLAUDE.md`.

**Set it up once (cross-platform).** Build the dir, then log the sandbox in
(auth is *not* copied from the host — a sandbox keeps its own credential):

<details open><summary><b>macOS / Linux</b> (and Windows→WSL)</summary>

```bash
cd eval
python -m memeval.claudecode.sandbox            # creates eval/.claude-sandbox/ + prints the login line
CLAUDE_CONFIG_DIR="$PWD/.claude-sandbox" claude  # then run /login inside it, once
```
</details>

<details><summary><b>Windows (native PowerShell)</b></summary>

```powershell
cd eval
python -m memeval.claudecode.sandbox             # creates .claude-sandbox\ + prints the login line
$env:CLAUDE_CONFIG_DIR = "$PWD\.claude-sandbox"
claude                                           # then run /login inside it, once
```

On **Windows→WSL** (the harness routes `claude` through WSL), use the WSL/bash
form above *inside the distro*; the harness translates the sandbox path to its
`/mnt/...` form automatically when it launches the in-WSL CLI.
</details>

After that one-time `/login`, the sandbox holds its own token and
`memeval-bench` **auto-detects and uses it** — no flags needed. Re-run
`python -m memeval.claudecode.sandbox` anytime to check status; it prints whether
the sandbox is logged in.

**What the sandbox isolates (and what it can't).** It removes everything in the
host `~/.claude`: your global `CLAUDE.md`, every installed/personal skill, custom
agents, `settings.json`, and MCP servers — verified, those are gone. It does
**not** remove Claude Code's **built-in skills** (`init`, `review`,
`security-review`, `code-review`, `verify`, `loop`, `schedule`, …) — those are
baked into the CLI binary, present in every config dir. The only flag that strips
them is `--bare`, which forces API-key auth and rejects the sandbox's OAuth login,
so we don't use it. For benchmarking this is the right trade: the confound we care
about (your personal skills/agents skewing the agent under test) is gone, and the
agent still authenticates via subscription. The CLI also auto-installs the official
plugin marketplace into a fresh dir on first run; its skills overlap the built-ins
and are immaterial to a bench run.

**Control it:**

| Env var | Effect |
|---|---|
| *(none)* | Use `eval/.claude-sandbox/` **iff it's been built**; otherwise fall back to the host `~/.claude` (unchanged behavior). |
| `MEMEVAL_SANDBOX_CONFIG_DIR=/path` | Use an explicit config dir (highest precedence) — e.g. share one sandbox across checkouts. |
| `MEMEVAL_SANDBOX=0` | Force-disable the sandbox for this run (use the host `~/.claude`). Also accepts `false`/`no`/`off`. |

> **Why auth isn't seeded from the host.** We tried copying
> `~/.claude/.credentials.json` into the sandbox; it doesn't work. On macOS the
> live token is in the OS keychain (the on-disk file is a stale leftover), and
> headless `claude -p` doesn't refresh an expired token — it sends it as-is and
> gets a 401. Copying the live keychain secret into a plaintext file is the only
> way to seed it, which we deliberately avoid. A one-time `/login` is the clean,
> portable answer.

## 3. The memory modes

| `--mode` | What memory the agent has |
|---|---|
| `builtin` | **Claude Code's own**: the task's prior sessions are written to `CLAUDE.md` and auto-loaded |
| `plugin` | **ours, in-harness**: an MCP server (`memory_recall` / `memory_remember`) over an OKF store, wired by the harness |
| `plugin-real` | **the shipping plugin, black box**: the real `plugin/cookbook_memory` package installed via `claude plugin install`, seeded with `memory-cli remember`, and driven through the plugin's own `recall` tool |
| `off` | none (baseline) — accepted explicitly, but **not** part of `--mode all` |

`--mode all` runs the head-to-head that matters: **builtin vs plugin**. `off` and
`plugin-real` are accepted but must be named explicitly (they are *not* part of `all`).

**Bench the shipping plugin (`plugin-real`):**

```bash
memeval-bench --benchmark longmemeval --mode plugin-real --model claude-haiku-4-5 --limit 20
```

Prereqs for `plugin-real`: build the sandbox config (`python -m memeval.claudecode.sandbox`),
`/login` once inside it, and install the plugin package so its `recall` tool and
`memory-cli` are importable (`pip install -e '../../plugin[mcp]'`). The harness then
performs the real `claude plugin install` and treats the plugin as a black box.

## 4. Quickstart

```bash
cd eval

# Offline smoke first (free, no claude, bundled fixtures) — proves the wiring:
memeval-bench --benchmark longmemeval --mode builtin \
    --path fixtures --limit 2 --results /tmp/cc.json

# Run ONE benchmark on its own (both memory modes), real data:
memeval-bench --benchmark memoryagentbench --mode all \
    --model claude-haiku-4-5 --out-dir runs/claudecode --results runs/claudecode/results.json

# Or the full comparison: all 5 benchmarks × {builtin, plugin}, real data,
# per-benchmark entry floors, $200 cap, raw artifacts written under runs/:
memeval-bench --benchmark all --mode all \
    --model claude-haiku-4-5 --out-dir runs/claudecode --results runs/claudecode/results.json
```

## 5. Run each benchmark

Run from the `eval/` directory. Each command below runs **one** benchmark on its
own with both memory modes (`--mode all` = builtin + plugin). Drop `--mode all`
for a single mode (e.g. `--mode plugin`). Entry counts default to each benchmark's
[long-memory floor](#6-how-many-entries); override with `--limit N`. List the ids
anytime with `memeval-bench --list-benchmarks`. (`memeval-bench` is the installed
short form of `python -m memeval.claudecode.run_bench` — use either.)

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

## 7. Where results are written

Each run produces results in three places (all on by default):

**a) Per-benchmark versioned files** — one file per benchmark, holding that
benchmark's runs (both modes), bucketed by the **memory-system version**:

```
results/v0.1/<benchmark>-<timestamp>.json     # {schema, memory_version, benchmark, timestamp, runs:[...]}
```

- Root via `--results-dir` (default `results`; `''` to skip), version bucket via
  `--results-version` (default `v0.1`, the `memeval.MEMORY_VERSION` constant).
- **Bump the version by 0.1 whenever you change the memory code/storage and
  re-run**, so each generation's results live in their own `v{X.Y}/` directory.
  Edit `MEMORY_VERSION` in `memeval/__init__.py` (or pass `--results-version`).
- `<timestamp>` is one UTC stamp per sweep (e.g. `20260620T193045Z`), shared by
  all of that sweep's benchmark files.

**b) Aggregate ledger** (`--results results.json`) — the flat run list the
GitHub Pages **Results page** and the `summary` scoreboard read.

**c) Raw per-run artifacts** (`--out-dir DIR`, optional) — everything a run
produced, for debugging/reproducibility:

```
DIR/
  <benchmark>__<mode>.record.json       # one run's row: metrics, entries_used/available/limit, cost
  <benchmark>__<mode>.trajectory.jsonl  # one JSON line per task (retrieve/generate/write steps)
  <mode>/<task_id>/                      # the agent's working dir for that task:
      CLAUDE.md      (builtin) | .mcp.json + memory/ + recall.jsonl  (plugin)
```

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
- **plugin-real**: installs the shipping `cookbook_memory` plugin for real, seeds it
  via `memory-cli remember`, lets the model call the plugin's own `recall` tool, and
  reads the plugin's recall log back for the memory metrics.
- `off` / `builtin` don't expose retrieval, so only **accuracy** is meaningful
  there; `plugin` / `plugin-real` report all four metrics.
- `cli._clean_env` strips API keys and, when a [sandbox](#sandboxed-config) is
  active, sets `CLAUDE_CONFIG_DIR` so the CLI ignores the host `~/.claude`. The
  WSL path carries both across the boundary via an in-WSL `env …` prefix
  (`sandbox.py` resolves the dir; `platform.to_wsl_path` translates it).

Plugin details + standalone (non-benchmark) use: [`plugin/README.md`](plugin/README.md).
Everything except the live `claude` call is covered by offline tests
(`python tests/test_smoke.py`, or `pytest -k claudecode`) using an injected fake
CLI runner — so you can verify the wiring with nothing installed.
