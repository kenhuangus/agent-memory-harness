<p align="center">
  <img src="assets/img/logo.svg" alt="Cookbook Memory" width="112">
</p>

# AI Agent Memory Harness (codename: Cookbook Memory)

**Repository:** <https://github.com/kenhuangus/agent-memory-harness> · **Live site:** <https://kenhuangus.github.io/agent-memory-harness/>

A project site (GitHub Pages) for a **persistent memory harness for long-running coding agents**.

The harness gives an agent self-curating memory so a smaller model (Haiku) can close the gap to a frontier
model (Opus 4.8) **without** memory, measured on public memory benchmarks. This repo holds the static website
that documents the plan, architecture, benchmarks, implementation contracts, and a results scoreboard.

## What's here

| Page | File | Contents |
|------|------|----------|
| Overview | `index.html` | Hypothesis, the four modules, the four metrics |
| Plan | `plan.html` | Problem, technical approach, scope, ownership, Gantt + milestones — with a **[PDF download](project-plan.pdf)** |
| Architecture | `architecture.html` | The diagram, data flows, the router, indexing |
| Benchmarks | `benchmarks.html` | The 5 public benchmarks + metric mapping, with links |
| Implementation | `implementation.html` | Schemas, storage interface, router/dreaming contracts, eval protocol |
| Results | `results.html` | Live scoreboard rendered from `results.json` (v0.1 results exist; see `results/`) |
| Collaborate | `collaborate.html` | Branch model, ownership, running the pipeline, PR/merge flow |

```
.
├── index.html  plan.html  architecture.html  benchmarks.html  implementation.html  results.html  collaborate.html
├── project-plan.md          # full plan (problem · approach · scope · ownership · timeline)
├── project-plan.pdf         # downloadable PDF of the plan
├── plan.md · prd.md · architecture.md  # the design contracts (what · why · how)
├── assets/
│   ├── css/style.css        # theme + all components
│   ├── js/main.js           # nav toggle, active link, reveal-on-scroll
│   └── img/architecture.svg # standalone architecture diagram
├── eval/                     # the evaluation harness (Python pkg `memeval`) — see "Run the benchmarks"
├── plugin/                   # the installable Cookbook Memory plugin (cookbook_memory) — see plugin/README.md
├── results/                  # committed benchmark results, bucketed by memory version (v0.1, v0.1-bm25, …)
├── docs/                     # ADRs and design records (docs/adrs/**)
├── tools/                    # helper scripts (dataset probes, etc.)
├── .nojekyll                # serve assets as-is (no Jekyll build)
└── README.md
```

The site is plain HTML/CSS/JS — **no build step, no dependencies**. Open `index.html` locally, or publish it
straight to GitHub Pages.

## Publish to GitHub Pages

1. Create a repository on GitHub (e.g. `agent-memory-harness`).
2. From this folder:

   ```bash
   git init
   git add .
   git commit -m "Add AI Agent Memory Harness project site"
   git branch -M main
   git remote add origin https://github.com/<you>/agent-memory-harness.git
   git push -u origin main
   ```

3. On GitHub: **Settings → Pages → Build and deployment → Source: Deploy from a branch**, pick `main` / `/ (root)`,
   and save.
4. The site goes live at `https://<you>.github.io/agent-memory-harness/` within a minute or two.

> All internal links are relative, so the site works at a project-pages subpath or at a user/organisation root
> without any changes.

## Benchmarks

The suite is positioned around **two in-scope benchmarks**:

- **SWE-Bench-CL** *(primary — CODE / continual learning)* — [paper](https://arxiv.org/abs/2507.00014) · [code](https://github.com/thomasjoshi/agents-never-forget)
- **VISTA** *(2nd benchmark — foresight × safety, memory poisoning / adaptation)* — [code](https://github.com/kenhuangus/vista-benchmark) · corpus CC-BY-4.0 (see [`eval/memeval/data/vista/ATTRIBUTION.md`](eval/memeval/data/vista/ATTRIBUTION.md))

### Legacy benchmarks (kept available, non-primary)

These four original memory benchmarks are **de-scoped to legacy/non-primary** — their loaders, evaluators, and tests remain in the code and they stay fully selectable (`--benchmark <id>`), they are simply no longer positioned as the headline suite:

- **MemoryAgentBench** — [paper](https://arxiv.org/abs/2507.05257) · [code](https://github.com/HUST-AI-HYZ/MemoryAgentBench) · [dataset](https://huggingface.co/datasets/ai-hyz/MemoryAgentBench)
- **LongMemEval** — [paper](https://arxiv.org/abs/2410.10813) · [code](https://github.com/xiaowu0162/LongMemEval) · [site](https://xiaowu0162.github.io/long-mem-eval/)
- **SWE-ContextBench** — [paper](https://arxiv.org/abs/2602.08316) · [dataset](https://huggingface.co/datasets/jiayuanz3/SWEContextBench) · [code](https://github.com/jiayuanz3/SWEContextBench)
- **ContextBench** (in-task retrieval quality) — [paper](https://arxiv.org/abs/2602.05892) · [dataset](https://huggingface.co/datasets/Contextbench/ContextBench) · [code](https://github.com/EuniAI/ContextBench)

See [`docs/adrs/ADR-eval-007-benchmark-suite-scope.md`](docs/adrs/ADR-eval-007-benchmark-suite-scope.md) for the scoping decision.

Complementary: [LoCoMo](https://arxiv.org/abs/2402.17753), [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified).

**Dataset schemas & sample records:** see [`benchmark-schema-sampledata.md`](benchmark-schema-sampledata.md) for each benchmark's source JSON schema, a truncated example record, and how it maps to the harness `Task` shape.

## Run the benchmarks (the `eval/` harness)

The evaluation code lives in [`eval/`](eval/) — a stdlib-first Python package
(`memeval`). You can run each benchmark **on its own**, or several together,
**locally through the Claude Code CLI**, comparing Claude Code's **built-in
memory** vs **our plugin memory**, on your Claude Code **subscription** (no API
key, no API billing). Installing puts a **`memeval-bench`** command on your PATH.

Set up once with [`uv`](https://docs.astral.sh/uv/) (Python 3.13; same on macOS / Linux /
WSL — see [`eval/README.md` → Setup](eval/README.md#setup-one-command--macos-linux-wsl)):

```bash
make setup                                       # .venv on 3.13 + harness + plugin (via uv)
npm install -g @anthropic-ai/claude-code         # the `claude` CLI (the agent under test)
```

Run the **single-stage SWE-Bench-CL pipeline** from the repo root. Each invocation
chooses one stage (`base`, `builtin`, `plugin-blank`, `plugin-accum`,
`plugin-dreamed`, or `plugin-primed`) over one sequence. `plugin-accum` and
`plugin-dreamed` require an existing non-empty memory store from a previous matching
run; the runner copies that source into the new run's own versioned namespace before
evaluating.

```bash
# via make
make pipeline                                    # interactive
make pipeline ARGS="--yes --stage base --sequence pytest-dev_pytest_sequence --limit 3 --budget-usd 5"

# or run the command directly (no make, no ARGS=) — uv finds ./.venv, or activate it first
uv run memeval-pipeline                          # interactive
uv run memeval-pipeline --yes --stage base --sequence pytest-dev_pytest_sequence --limit 3 --budget-usd 5
memeval-pipeline --help                          # all flags
```

Inspect what a run actually saved with the **router memory-inspector** web UI (browse the
plugin's memories + evaluate how the router routed them — see
[`ui/README.md`](ui/README.md)):

```bash
make viewer                                      # newest results/v*/_memory substrate
make viewer ARGS="--seed --open"                 # synthetic demo corpus + open browser
make viewer ARGS="--store /path/to/_memory"      # a specific store (or pick one in-UI via Browse…)
```

For the individual `memeval-bench` commands below, prefix with `uv run` (or activate `.venv`):

```bash
cd eval
memeval-bench --list-benchmarks                  # see the five ids (offline, no claude)

# 1) offline smoke first (free, no claude, bundled fixtures):
memeval-bench --benchmark longmemeval --mode builtin --path fixtures --limit 2 --results /tmp/cc.json

# 2) run ONE benchmark on its own (both memory modes), real data:
memeval-bench --benchmark memoryagentbench --mode all \
    --model claude-haiku-4-5 --out-dir ../runs/claudecode \
    --results ../runs/claudecode/results.json

# 3) or the full comparison: all 5 benchmarks x {builtin, plugin}, per-benchmark entry floors:
memeval-bench --benchmark all --mode all \
    --model claude-haiku-4-5 --out-dir ../runs/claudecode \
    --results ../runs/claudecode/results.json

# 4) read the verdict (our memory vs Claude Code's built-in memory, per benchmark):
python -m memeval.results summary --path ../runs/claudecode/results.json
```

`memeval-bench …` is the installed short form of `python -m memeval.claudecode.run_bench …`
(use the module form if you didn't `pip install`). Run `memeval-bench --help` for every flag.

- **Run any benchmark separately** — `--benchmark <id>` (one of the five from
  `--list-benchmarks`) runs just that one; `--benchmark all` runs them in sequence.
- **Auth is subscription-only** — `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` are
  stripped from every `claude` invocation; runs use your Claude Code OAuth login.
- **Cross-platform**, auto-detected: macOS · Linux · Windows · Windows→WSL.
- **`--mode`** is `off` (no memory baseline) | `builtin` (Claude Code's own `CLAUDE.md`
  memory) | `plugin` (our in-harness OKF-backed MCP memory) | `plugin-real` (the shipping
  `plugin/cookbook_memory` package installed via a real `claude plugin install` and driven
  as a black box) | `all`. `--mode all` runs **builtin + plugin only** — name `off` and
  `plugin-real` explicitly. CODE tasks are solved by the Claude Code CLI acting
  as a real coding agent (`--code-mode agentic`, default — genuine checkout, edit,
  run tests) and graded on the host by local test execution (`LocalExecGrader`),
  or by retrieval metrics for ContextBench — **no extra install** (see
  `eval/PROTOCOL.md` §5).

Full guides: the per-developer, per-benchmark runbook is
[`eval/memeval/claudecode/README.md`](eval/memeval/claudecode/README.md); the
harness architecture, offline runs, and metrics are in [`eval/README.md`](eval/README.md).

## Source

Generated from a design conversation about the memory-harness project. Content is documentation of the plan;
benchmark links were verified against public listings at build time — confirm dataset versions/licenses on each
source page before use.
