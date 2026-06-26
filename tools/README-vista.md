# VISTA benchmark run scripts

Faithful, reproducible runner for the VISTA benchmark — the same commands, flags,
and env that produced the VISTA 97-test-split run. Wraps
[`tools/run_vista.sh`](run_vista.sh) with Makefile convenience targets.

## What it runs

For each arm, `run_vista.sh` invokes:

```
python -m memeval.claudecode.run_bench \
  --benchmark vista --mode <mode> --limit <limit> \
  --model claude-haiku-4-5 --grader none --out-dir <out>
```

with `--plugin-workers <n>` added for `plugin-real`. VISTA self-scores, so
`--grader none` is intentional. The model is pinned to `claude-haiku-4-5`.

## Usage

```
bash tools/run_vista.sh <mode> <split> <limit> [workers]
  mode    : off | builtin | plugin-real
  split   : train | dev | test | challenge | all
  limit   : 0 = all tasks in the split, or a positive integer cap
  workers : plugin-real only; --plugin-workers value (default 4)
```

The 97-test-split run was:

```
bash tools/run_vista.sh plugin-real test 0 4   # plugin arm
bash tools/run_vista.sh builtin     test 0     # builtin arm
```

### Make targets

```
make vista-off          SPLIT=test LIMIT=0
make vista-builtin      SPLIT=test LIMIT=0
make vista-plugin-real  SPLIT=test LIMIT=0 WORKERS=4
make vista-smoke        # plugin-real dev limit 8 workers 4
```

## Environment prerequisites

Run from WSL/Linux (the originals used `/home/kenhu/vista-venv`; the script
prepends it to `PATH` if present). `REPO` is derived from the script location.

- **`OPENROUTER_API_KEY`** (plugin-real only) — powers the daydream write path.
  Sourced automatically from the WSL claude-managed block in `~/.profile` /
  `~/.bashrc`. The driver log prints `keylen=`; it must be non-zero (e.g. 73).
  **Without it, daydream silently writes 0 memories.**
- **`MEMEVAL_SANDBOX_CONFIG_DIR`** (plugin-real only) — a *logged-in* Claude
  sandbox config dir for auth. Read from env; the script errors with guidance
  if unset or not a directory. The 97-split run used
  `.../agent-memory-harness-vcs/eval/.claude-sandbox-vcs`. Export your own:
  ```
  export MEMEVAL_SANDBOX_CONFIG_DIR=/path/to/eval/.claude-sandbox
  ```

### Detaching a long run (Windows)

```powershell
Start-Process wsl -ArgumentList '-d','Ubuntu','--','bash','-lc', `
  'cd /mnt/c/Users/kenhu/agent-memory-harness && bash tools/run_vista.sh plugin-real test 0 4' `
  -WindowStyle Hidden
```

## Results

Everything lands under `runs/vista/<split>/<mode>/`:

- `driver.log` — start/exit timestamps, the exact command, and `keylen`
- `run.out` / `run.err` — run_bench stdout/stderr
- `dream_full.log` — per-store `dream --all` consolidation output (plugin-real)
- `record.json` — per-task records; check `reliability` and `native.metrics`
- `_memory/<task>/.cookbook-memory/` — the persisted plugin stores

## Gotchas

- `--plugin-workers` is safe only post-#168; earlier the MCP connection degraded
  under concurrency.
- daydream writes **0** memories without `OPENROUTER_API_KEY` — verify
  `keylen=73` in `driver.log`.
- `--grader none` is correct: VISTA self-scores, no external grader needed.
