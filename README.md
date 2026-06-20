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
| Benchmarks | `benchmarks.html` | The 4 public benchmarks + metric mapping, with links |
| Implementation | `implementation.html` | Schemas, storage interface, router/dreaming contracts, eval protocol |
| Results | `results.html` | Empty scoreboard template to fill in after the runs |

```
.
├── index.html  plan.html  architecture.html  benchmarks.html  implementation.html  results.html
├── project-plan.md          # full plan (problem · approach · scope · ownership · timeline)
├── project-plan.pdf         # downloadable PDF of the plan
├── assets/
│   ├── css/style.css        # theme + all components
│   ├── js/main.js           # nav toggle, active link, reveal-on-scroll
│   └── img/architecture.svg # standalone architecture diagram
├── eval/                     # the evaluation harness (Python pkg `memeval`) — see "Run the benchmarks"
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

## The five benchmarks

- **MemoryAgentBench** — [paper](https://arxiv.org/abs/2507.05257) · [code](https://github.com/HUST-AI-HYZ/MemoryAgentBench) · [dataset](https://huggingface.co/datasets/ai-hyz/MemoryAgentBench)
- **LongMemEval** — [paper](https://arxiv.org/abs/2410.10813) · [code](https://github.com/xiaowu0162/LongMemEval) · [site](https://xiaowu0162.github.io/long-mem-eval/)
- **SWE-ContextBench** — [paper](https://arxiv.org/abs/2602.08316) · [dataset](https://huggingface.co/datasets/jiayuanz3/SWEContextBench) · [code](https://github.com/jiayuanz3/SWEContextBench)
- **SWE-Bench-CL** — [paper](https://arxiv.org/abs/2507.00014) · [code](https://github.com/thomasjoshi/agents-never-forget)
- **ContextBench** (in-task retrieval quality) — [paper](https://arxiv.org/abs/2602.05892) · [dataset](https://huggingface.co/datasets/Contextbench/ContextBench) · [code](https://github.com/EuniAI/ContextBench)

Complementary: [LoCoMo](https://arxiv.org/abs/2402.17753), [SWE-bench](https://www.swebench.com).

## Run the benchmarks (the `eval/` harness)

The evaluation code lives in [`eval/`](eval/) — a stdlib-first Python package
(`memeval`). You can run all five benchmarks **locally through the Claude Code
CLI**, comparing Claude Code's **built-in memory** vs **our plugin memory**, on
your Claude Code **subscription** (no API key, no API billing).

```bash
cd eval
pip install -e ".[claudecode,hf]"               # harness + MCP memory plugin + dataset loaders
npm install -g @anthropic-ai/claude-code         # the `claude` CLI (the agent under test)

# 1) offline smoke first (free, no claude, bundled fixtures):
python -m memeval.claudecode.run_bench --benchmark longmemeval --mode builtin \
    --path fixtures --limit 2 --results /tmp/cc.json

# 2) the full comparison: 5 benchmarks x {builtin, plugin}, per-benchmark entry floors:
python -m memeval.claudecode.run_bench --benchmark all --mode all \
    --model claude-haiku-4-5 --out-dir ../runs/claudecode \
    --results ../runs/claudecode/results.json

# 3) read the verdict (our memory vs Claude Code's built-in memory, per benchmark):
python -m memeval.results summary --path ../runs/claudecode/results.json
```

- **Auth is subscription-only** — `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` are
  stripped from every `claude` invocation; runs use your Claude Code OAuth login.
- **Cross-platform**, auto-detected: macOS · Linux · Windows · Windows→WSL.
- **`--mode`** is `builtin` (Claude Code's own `CLAUDE.md` memory) | `plugin` (our
  OKF-backed MCP memory) | `all`. CODE benchmarks need the SWE-bench Docker grader
  (`pip install -e ".[swebench]"`) to score accuracy.

Full guides: the per-developer, per-benchmark runbook is
[`eval/memeval/claudecode/README.md`](eval/memeval/claudecode/README.md); the
harness architecture, offline runs, and metrics are in [`eval/README.md`](eval/README.md).

## Source

Generated from a design conversation about the memory-harness project. Content is documentation of the plan;
benchmark links were verified against public listings at build time — confirm dataset versions/licenses on each
source page before use.
