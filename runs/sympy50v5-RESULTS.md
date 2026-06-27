# sympy50 · V5 daydream — memory benchmark results

Benchmark: **SWE-bench-CL `sympy_sympy_sequence`, first 50 tasks** (identical task-ID
set across all solvers). Daydream extraction variant: **V5**. Grading: SWE-bench host
grader (`claude-sonnet-4-6` as grader). Three memory arms per solver:

- **base** — memory off (no recall, no store)
- **builtin** — solver's native/session memory
- **plugin** — cookbook memory plugin (recall + store + V5 daydream consolidation)

## Headline metrics (resolved / 50)

| Solver | base (no mem) | builtin | plugin (cookbook V5) | memories stored |
|---|---|---|---|---|
| **grok** (xAI) | **42 (84%)** | **40 (80%)** | **38 (76%)** | 20 |
| **claude CLI** | 10 (20%), $0.637 | not run | not run | — |
| **agy** (Antigravity / Gemini) | ~11 partial | — | — | — |

### Notes per solver
- **grok** — the only solver with a complete, uncontaminated 3-arm sweep. Clean signal.
- **claude CLI** — stopped after the base arm to conserve subscription credits; builtin/plugin not run.
- **agy** — gemini-3.1-pro daily quota exhausted after ~16 tasks, so base is a quota-capped
  floor (~11/50) and builtin/plugin could not complete. agy itself works mechanically
  (the fix was passing `--add-dir <checkout>` to the headless CLI; verified 2/2 on a smoke gate).

## Verdict

On grok — the clean run — **cookbook memory did not help; it monotonically hurt**:

```
base 42  >  builtin 40  >  plugin (cookbook V5) 38
```

Even with V5's cleaner, transferable-lesson extraction (20 well-formed memories stored),
recalled memory did not lift solve rate. This reproduces the earlier 15-task finding at
full 50-task scale. The gap is on the **recall / injection** side (which memories get
selected and how they enter the solving turn), not on extraction quality — recalled
context is, if anything, slightly distracting the solver on these tasks.

## Reproduce

Committed harness (this repo):
- `eval/memeval/claudecode/grok_runner.py` — grok solver adapter
- `eval/memeval/claudecode/pipeline.py` — claude pipeline
- `eval/memeval/dreaming/prompts.py` — V5 extraction prompt (`DREAM_EXTRACTION_VARIANT=V5`)
- `eval/memeval/grader_swebench.py` — SWE-bench grader

Run drivers / adapters (under `runs/`):
- grok: `runs/sympy50v5-grok/driver.sh` + `runs/sympy50v5-grok/run_grok.py`
- claude: `runs/sympy50v5-claude/driver.sh`
- agy: `runs/sympy50v5-agy/driver.sh` + `runs/sympy3-agy/agy_runner.py`
- harvest / comparison: `runs/_harvest_full50.py`

## Data in this branch

- `runs/sympy50v5-grok/{base,builtin,plugin}/results.json` + `status.txt`
- `runs/sympy50v5-grok/plugin/.cookbook-memory/memory.db` — 20 stored cookbook memories
- `runs/sympy50v5-claude/RESULT.md`
- `runs/sympy50v5-agy/base/results.json`, `runs/sympy50v5-agy/RESULT.md`

(agy repo checkouts ~2.5 GB are intentionally excluded — only result artifacts are included.)
