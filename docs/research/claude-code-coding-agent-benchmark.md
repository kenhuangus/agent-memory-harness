# Claude Code CLI as a real coding agent + Docker-free CODE benchmarking

> Status: investigation / design. No production code is changed by this document.
> Scope: the three CODE benchmarks in `agent-memory-harness`
> (`swe_contextbench`, `swe_bench_cl`, `contextbench`).
> Branch: `docs/cc-coding-agent-benchmark` off `main@d132ba6`.

## 0. TL;DR

Today the CODE branch asks `claude -p` for a **blind unified diff** — no repo, no
file access, no tests, no memory — and then (optionally) grades it in a per-task
SWE-bench Docker container. That is the path in
[`agent.py` `solve()` CODE branch](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/agent.py#L210-L216),
which calls
[`_build_code_prompt`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/agent.py#L485-L498)
("no checkout is provided") and
[`_extract_diff`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/agent.py#L524-L606).

**Recommendation, per benchmark:**

| Benchmark | Run Claude Code as… | Default grading (no Docker) | Docker still needed? |
|---|---|---|---|
| **contextbench** | a *retriever* (Read/Grep/Glob over a real checkout) — OR keep it patch-free | **retrieval precision/recall/F1 over `gold_context` spans** (the benchmark's native metric — **no execution at all**) | **No.** Execution is not its metric. |
| **swe_bench_cl** | a *real coding agent* (Read/Edit/Bash on a checkout, iterate, run tests) | **local per-task venv exec** of `FAIL_TO_PASS`/`PASS_TO_PASS` (SWE-bench resolved rule), harness-applied gold `test_patch`; fall back to `overlap_grader` when env build fails | **Optional** — opt-in for a reproducible headline number. |
| **swe_contextbench** | a *real coding agent* (same loop) | same local per-task venv exec; many repos won't build cleanly so expect partial coverage + honest `None` for the rest | **Optional**, same as above. Docker is the only *faithful* multi-repo/multi-language story. |

**Headline:** drive `claude -p` with `--permission-mode acceptEdits` (or
`bypassPermissions`) and **no `allowedTools` restriction** (unrestricted = its
full native Read/Edit/Bash toolset) in a working checkout at `base_commit`,
let it iterate, then `git diff` is the produced patch. Grade the diff with a new
**default local-exec grader** (venv/uv, no container) using the standard
SWE-bench resolved rule; keep `SWEBenchDockerGrader` as the opt-in reproducible
fallback. For ContextBench, skip patch grading entirely and score
**retrieval quality over the gold spans** — it needs neither Docker nor a checkout.

This is **additive**: a new `solve_code_agentic` path and a new
`LocalExecGrader`, leaving the existing blind-diff path and `SWEBenchDockerGrader`
untouched and selectable.

---

## 1. How CODE tasks are solved today (the "blind patch")

The whole current CODE path:

[`ClaudeCodeAgent.solve()`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/agent.py#L202-L232):

```python
if task.kind == TaskKind.CODE:
    code_prompt = _build_code_prompt(task)
    res = self._run(code_prompt, run_dir, _SYS_CODE,
                    mcp_config=None, allowed_tools=None)
    ctx.record_generate(res.text, res.tokens_in, res.tokens_out, model_name=self.model)
    return _extract_diff(res.text)
```

Key facts that motivate the redesign:

- **No repo is checked out.** `_build_code_prompt` literally documents "no
  checkout is provided" and only hands the model `repo`, `base_commit`, and the
  issue text as *strings*
  ([L485-L498](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/agent.py#L485-L498)).
- **No agentic tools.** `mcp_config=None, allowed_tools=None` plus the system
  prompt `_SYS_CODE` ("Output ONLY a unified diff … Do NOT include any
  explanation")
  ([L52-L56](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/agent.py#L52-L56))
  forces a single-shot text completion. Note `_SYS_CODE` *disables* the very tool
  use we want.
- **The CODE branch is reached BEFORE the memory-mode dispatch** — so the
  benchmark's whole point (memory modes) never touches CODE tasks. `builtin` /
  `plugin` / `plugin-real` are bypassed for CODE; the agent name still claims a
  memory mode, but a CODE run with `--mode plugin` does exactly the same thing as
  `--mode off`. This is the "PRD compliance" gap: CODE benches do not exercise
  memory at all today.
- **`record_generate` only** — no `record_retrieve`, so the
  recency/relevancy/efficiency memory metrics are structurally zero for CODE.
- The diff is grade-able: `solve()` returns a string, which the harness coerces
  to `prediction` in
  [`_coerce_result`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/agent.py#L580-L585)
  and routes to the grader in
  [`_grade`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/agent.py#L588-L595).

### What the CLI invocation already supports (reuse points)

The CLI layer is already capable of the agentic loop; we don't need new flags.

- [`build_argv`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/cli.py#L76-L104)
  builds `claude -p <prompt> --output-format json --permission-mode <mode>
  [--model …] [--mcp-config …] [--allowedTools …] [--append-system-prompt …]`,
  native or `wsl -d <distro> --cd <wslpath> -- …` with path translation.
- **`permission_mode` defaults to `bypassPermissions`**
  ([cli.py L80](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/cli.py#L80)),
  so Claude Code may already Edit + Bash without prompting; and when
  `allowed_tools` is `None` the `--allowedTools` flag is omitted entirely
  ([cli.py L69-L70](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/cli.py#L69-L70)),
  i.e. **the full native toolset is available**. The only thing stopping the
  current CODE path from editing files is `_SYS_CODE` + the empty `run_dir`.
- The run executes with `cwd=run_dir`
  ([`_run`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/agent.py#L432-L440)
  → `run_claude(..., cwd=run_dir)`), so if `run_dir` *is* a checkout, the agent's
  native Read/Edit/Bash operate on real files with zero extra plumbing.
- `strip_api_key=True` keeps every run on the **subscription** (OAuth), never an
  API key — preserved for the new path.
- `timeout` is plumbed end to end (default 600s in `run_bench`).

### The sandbox (reuse point)

[`sandbox.py`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/sandbox.py)
already gives a clean `CLAUDE_CONFIG_DIR` with no host skills/agents/`CLAUDE.md`,
native `claude plugin install` of the cookbook-memory plugin
([`install_plugin_bundle`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/sandbox.py#L108-L146),
[`setup_real_plugin`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/sandbox.py#L195-L217)),
and the `PATH`/`CLAUDE_PROJECT_DIR` env a plugin's MCP server needs
([`plugin_runtime_env`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/sandbox.py#L179-L192)).
The agentic CODE path reuses all of it unchanged — it just runs in a checkout
working dir instead of an empty one.

### What each CODE task actually carries

From the loaders and fixtures, every CODE `Task` carries `repo`, `base_commit`,
gold `patch`, `test_patch`, `fail_to_pass`, `pass_to_pass`, plus benchmark-specific
"memory":

- **ContextBench** —
  [loader](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/loaders/contextbench.py#L149-L212):
  `sessions` = one Session **per gold-context span** (`file:start-end`),
  `gold_memory_ids` = **every** span id. Its native metric is *in-task retrieval
  quality* over those spans — recall/precision/F1 — not patch success.
- **SWE-ContextBench** —
  [loader](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/loaders/swe_contextbench.py#L144-L217):
  `group_id` groups related issues; prior/sibling issue context becomes `sessions`.
  Patch+tests is its real scoring.
- **SWE-Bench-CL** —
  [loader](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/loaders/swe_bench_cl.py#L187-L276):
  `group_id` = per-repo chronological **sequence**; `sessions` = the agent's own
  **prior solved issues in the sequence** (`"<problem>\n\n[solution]\n<patch>"`),
  `gold_memory_ids` = those prior issues. This is the benchmark that most wants a
  real agentic loop + memory.

The `Task` schema already has every field we need
([schema.py L143-L164](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/schema.py#L143-L164)),
and `AgentResult` already supports returning `(prediction, patch, success)` where
**`success` overrides the harness grader "e.g. it ran the tests itself"**
([agent.py L82-L94](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/agent.py#L82-L94)).
The harness is already shaped for both an agent-produced patch and an
agent-self-graded result — no schema change required.

---

## 2. The agent-as-real-coder loop

Concretely, a new `solve_code_agentic(task, ctx)` does:

1. **Provision a checkout** of `task.repo` at `task.base_commit` into `run_dir`
   (the CLI already runs with `cwd=run_dir`). Strategies, cheapest first:
   - **Cached bare-repo snapshot** (recommended default): keep a local mirror per
     repo under `~/.cache/memeval/repos/<owner>__<name>.git` and
     `git worktree add` / `git clone --shared` at `base_commit` per task. First
     task on a repo pays the clone; the rest are near-instant and offline.
   - **Shallow fetch of one commit**:
     `git init && git remote add origin https://github.com/<repo> &&
      git fetch --depth 1 origin <base_commit> && git checkout FETCH_HEAD`.
     Avoids full history; needs network and a fetch-by-SHA-capable remote
     (GitHub supports it when the SHA is reachable).
   - **Full `git clone` then `git checkout <base_commit>`**: simplest, slowest,
     most bandwidth. Fine for small runs.
   - Put this behind a small `checkout.py` helper (`prepare_checkout(task,
     dest) -> Path`) so the strategy is swappable and the offline test can inject
     a stub repo instead of cloning. **The harness applies nothing yet** — the
     working tree is exactly `base_commit`, gold `test_patch` is NOT applied
     (that happens only at grade time; see §3b).

2. **Prompt** the agent with the problem statement + repo context + an
   instruction to *edit files and run tests*, NOT to print a diff. A new
   `_SYS_CODE_AGENT` replaces `_SYS_CODE` for this path, e.g.:
   > "You are a software engineer working in a real checkout of `<repo>` at
   > `<base_commit>`. Read the code, make the necessary edits to resolve the
   > issue, and run the project's tests to validate. Do not print a diff; edit
   > the files directly. When done, stop."

3. **Invoke** `claude -p` via the *existing* runner with:
   - `cwd = run_dir` (the checkout) — native Read/Edit/Bash act on real files;
   - `permission_mode = "acceptEdits"` (auto-accept edits, still gate other
     dangerous ops) — **or** `bypassPermissions` (current default) for fully
     unattended runs;
   - `allowed_tools = None` → no `--allowedTools` flag → full native toolset
     (Read/Edit/Write/Bash/Grep/Glob), exactly what `build_argv` already does;
   - `append_system_prompt = _SYS_CODE_AGENT`;
   - `timeout` from the agent (already plumbed) for a wall-clock cap;
   - cost control via the existing `CostTracker` budget + the `--model` flag
     (default Haiku for cheap iteration, Sonnet for a quality run).

   > **Turn/cost limits caveat (flag to verify):** headless `claude -p` runs to
   > completion of one prompt; there is no `--max-turns` flag in the current
   > runner. The wall-clock `timeout` is the hard stop. If a per-task turn cap is
   > wanted, it must be added to `_flags` (verify the installed CLI version
   > actually supports `--max-turns` before relying on it). For the primed/MCP
   > path, `run_claude_primed` already drives multi-message stream-json, but the
   > agentic coding loop only needs one rich turn.

4. **Capture the patch**: after the run, `git -C run_dir add -A &&
   git -C run_dir diff --cached <base_commit>` (or `git diff` against the clean
   tree) → the unified diff the agent produced. Reuse the existing
   `_extract_diff` is *not* needed here — we read the real `git diff`, which is
   already a clean patch. Return `AgentResult(prediction=diff, patch=diff)`.

5. **Memory wiring** (see §4): in `plugin` / `plugin-real` / `builtin` modes,
   seed the task's `sessions` into memory (prior solved issues for SWE-Bench-CL)
   and let the agent recall them — recording `ctx.record_retrieve(...)` exactly
   like the QA plugin path does. This is what finally makes CODE benches exercise
   memory.

### Where it slots in (named integration points)

- **`eval/memeval/claudecode/agent.py`** — in
  [`solve()`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/agent.py#L202-L216),
  branch the CODE case on a new agent flag `code_mode` (`"blind"` | `"agentic"`,
  default `"blind"` to preserve current behavior):
  ```python
  if task.kind == TaskKind.CODE:
      if self.code_mode == "agentic":
          return self._solve_code_agentic(task, ctx, run_dir)
      # else: existing blind path, byte-identical
  ```
  Add `_solve_code_agentic`, `_SYS_CODE_AGENT`; do NOT touch `_build_code_prompt`
  / `_extract_diff` / the QA path.
- **New `eval/memeval/claudecode/checkout.py`** — `prepare_checkout(task, dest,
  *, strategy, runtime) -> Path` and `capture_diff(checkout, base_commit) -> str`.
  Pure-ish, injectable git runner so the offline test uses a stub repo.
- **`eval/memeval/claudecode/run_bench.py`** — add `--code-mode
  {blind,agentic}` (default `blind`); pass to `ClaudeCodeAgent(...)`
  ([constructor](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/agent.py#L163-L199)
  and the call at
  [L145-L146](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/run_bench.py#L145-L146)).
- **`eval/memeval/grader.py`** — add `LocalExecGrader` (see §3a) and register it
  in [`get_grader`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/grader.py#L293-L307);
  wire `"local"` as a `--grader` choice in
  [`_make_grader`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/run_bench.py#L68-L93)
  and make it (or retrieval-only) the default for CODE.

Reuse: `sandbox.active_config_dir`, `plugin_runtime_env`, `setup_real_plugin`,
`run_claude` / `run_claude_primed`, `build_argv`, `platform.detect`/`to_wsl_path`
— all unchanged.

---

## 3. Grading WITHOUT Docker — options evaluated honestly

### (a) Local per-task environment (venv/conda/uv), no container

Build a per-task environment on the host (or WSL), apply the **gold `test_patch`**
(the harness applies it, never the agent — see (b)), run `FAIL_TO_PASS` and
`PASS_TO_PASS`, and apply the **SWE-bench resolved rule**: resolved iff *every*
`FAIL_TO_PASS` now passes AND *every* `PASS_TO_PASS` still passes. The pure
report-interpretation logic already exists and is reusable:
[`resolved_from_report`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/grader.py#L65-L111)
already derives resolved/not from a `tests_status` dict — `LocalExecGrader` only
needs to *produce* that dict from a local `pytest`/`unittest` run.

Sketch of `LocalExecGrader.__call__(task, prediction)`:
1. fresh checkout at `base_commit` (reuse `checkout.py`) into a temp dir;
2. apply the agent's `prediction` patch (`git apply`); if it doesn't apply → `False`;
3. apply gold `task.test_patch` (`git apply`); install deps
   (`uv venv && uv pip install -e .` or a per-repo recipe), with a timeout;
4. run the named tests, parse pass/fail into a `tests_status` dict;
5. `return resolved_from_report({task_id: {"tests_status": …}}, instance_id_of(task))`.

**Feasibility — be candid:**

- **This is exactly why SWE-bench ships Docker.** Reproducible dependency
  resolution across hundreds of repos × many Python/library versions (and, for
  SWE-ContextBench, **9 languages**) is the hard part. A host venv will:
  - hit version conflicts (a 2019 repo wanting an old NumPy that won't build on
    a modern toolchain),
  - need system libs (C/Rust/FORTRAN toolchains, headers) the host may lack,
  - be **non-reproducible across machines** (different OS libs → different pass
    sets), and **leak state** between tasks if envs aren't isolated.
- SWE-bench-CL / SWE-ContextBench derive from **SWE-bench Verified**, whose
  per-instance environment setup is non-trivial and is precisely what the Docker
  images encode. Reconstructing that per-instance setup *without* the image means
  reimplementing the `environment_setup_commit` + install recipe per repo.
- **Multilingual** SWE-ContextBench is the worst case: a single host can't
  reasonably hold toolchains for 9 languages with correct per-repo versions.

**Verdict on (a):** viable as a *default best-effort* grader **for Python repos
that build cleanly**, with a hard rule: on any env-build/test-collection failure,
return `None` (UNGRADED) — never `False`. That keeps accuracy honest (the harness
already excludes `None` from the denominator and documents `n_errors` /
`memory_reached`, see
[`_assemble` metadata](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/agent.py#L386-L394)).
Expect **partial coverage**; report "resolved out of *gradable*" alongside
coverage, and do NOT present a local-exec number as comparable to an official
SWE-bench leaderboard score.

### (b) Let Claude run the tests itself and report

In the agentic loop the agent already runs tests in its checkout to validate its
fix — that's good for *solving*. But using the agent's own "tests pass" as the
**grade** has a clear trust problem: the agent could (a) edit the tests, (b)
fool itself, or (c) simply assert success. `AgentResult.success` exists for this
("it ran the tests itself",
[agent.py L86-L88](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/agent.py#L82-L94))
and *would* override the grader — which is exactly the footgun.

**Mitigation (the only acceptable form):** the **harness**, not the agent, owns
the verdict. Concretely:
- the agentic loop returns `AgentResult(prediction=diff, patch=diff,
  success=None)` — i.e. it does **not** self-grade;
- the harness then re-checks out a **clean** `base_commit`, applies the agent's
  diff, applies the **gold `test_patch`** itself (so the agent can't have
  weakened the tests), and runs the named tests (this is grader (a) or Docker).
- the agent's own test runs are kept only as trajectory signal / for the agent's
  iteration, never as the score.

**Verdict on (b):** use Claude's test-running to *drive the fix*, never to
*grade it*. `forced_success` should stay reserved for trusted offline doubles
(e.g. the EchoAgent test), not real model runs.

### (c) Retrieval-only / no-execution grading (the native metric for some benches)

**ContextBench's native metric is retrieval quality, not patch success.** The
loader makes the gold context spans the retrievable units and marks every span
gold
([contextbench.py L149-L212](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/loaders/contextbench.py#L149-L212);
docstring "It measures retrieval **recall, precision and efficiency** … rather
than final patch success",
[L11-L13](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/loaders/contextbench.py#L1-L13)).
The harness already computes relevancy / recency / efficiency from
`ctx.record_retrieve` and `gold_memory_ids`. So for ContextBench:

- Run Claude Code as a **retriever**: either (i) over a real checkout with
  Read/Grep/Glob, capturing which files/line-ranges it opened, and scoring those
  against the gold spans (precision/recall/F1 over `file:start-end`); or (ii) the
  existing memory-mode retrieval path where the gold spans are seeded as memory
  and recall is scored directly — **this is what the harness already does for QA
  plugin mode** and needs *zero* execution and *zero* Docker.
- This makes ContextBench a first-class memory/retrieval benchmark with **no
  container, no test run, no checkout strictly required** (option (ii)).

Map of "needs test execution?" across the three CODE benches:

| Benchmark | Native scoring | Needs test execution? | Docker-free path |
|---|---|---|---|
| **contextbench** | retrieval recall/precision/F1 over gold spans | **No** | retrieval metric (already in harness) |
| **swe_contextbench** | patch resolves (F2P/P2P) | **Yes** | local-exec (best-effort) or Docker |
| **swe_bench_cl** | patch resolves (F2P/P2P), continual | **Yes** | local-exec (best-effort) or Docker |

### (d) Docker optional / fallback

Keep
[`SWEBenchDockerGrader`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/grader.py#L137-L254)
exactly as is — it is the only **faithful, reproducible** grade for the two
execution benches, and it already degrades gracefully (`on_unavailable='skip'`
→ `None`) and detects a dead daemon
([`_is_docker_unavailable`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/grader.py#L261-L287)).
Change only the **default**: today `_make_grader`'s `auto` → Docker for CODE
([run_bench.py L80-L93](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/run_bench.py#L68-L93)).
New `auto`:
- contextbench → retrieval-only (grader `None`; success via retrieval metric);
- swe_contextbench / swe_bench_cl → `local` (LocalExecGrader, best-effort),
  with `--grader docker` the opt-in for a publishable number.

> Note: `swebench` is **Linux-only** (imports `resource`); on Windows the Docker
> grader must run from WSL — already documented in the grader's error text. The
> local-exec grader has the same Windows-friendliness caveat for any repo needing
> a POSIX toolchain → prefer WSL for execution grading on this machine.

---

## 4. Memory angle — making CODE benches exercise memory

Today CODE bypasses memory entirely (§1). The agentic path fixes this by running
the **same memory wiring the QA path already has**, before/around the coding turn:

- **builtin**: write the task's `sessions` as `sessions/*.md` in the checkout
  (reuse
  [`_write_session_files`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/agent.py#L609-L648))
  and prepend `_BUILTIN_PREFIX` so the agent greps prior solved issues
  (SWE-Bench-CL's `sessions` are exactly "prior problem + solution"). Claude
  Code's native Grep/Read over those files = its built-in memory.
- **plugin** / **plugin-real**: seed the OKF store / cookbook store with the
  task's `sessions` and instruct the agent to `memory_recall` / `recall` the
  issue before coding — reuse
  [`_solve_plugin`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/agent.py#L235-L286)
  /
  [`_solve_plugin_real`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/agent.py#L337-L370)
  seeding + the retrieval-attribution loop verbatim, just with the coding prompt
  and a checkout `cwd`.
- The recall-attribution (`ctx.record_retrieve(...)`) is what populates
  recency/relevancy/efficiency — so the four metrics become meaningful for CODE
  for the first time.

This is the concrete realization of "prior solved issues (SWE-Bench-CL) recalled
into the agent's context": the group-aware draw already lands whole sequences
([`_select_group_aware`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/agent.py#L598-L609),
default-on for `swe_bench_cl`/`swe_contextbench` via
[`_GROUP_AWARE`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/run_bench.py#L60)),
so priors actually accumulate.

> Per the project's standing rule, this benchmarks the team's memory mechanism
> **as-is** — the agentic loop only changes *how the task is run and graded*, not
> the memory plugin.

---

## 5. Trade-offs table

Across reproducibility, fidelity to the real benchmark, setup cost, Windows/WSL
friendliness, speed, and trust:

| Dimension | Docker (SWE-bench) | Local-exec (venv/uv) | Retrieval-only (no exec) | Agent self-report |
|---|---|---|---|---|
| **Reproducibility** | High — pinned per-instance images | **Low–Med** — host/OS-dependent, version drift | High — deterministic over gold spans | Low — depends on agent's own run |
| **Fidelity to benchmark** | High (the canonical rule) | Med — same rule *when env builds*; partial coverage | **High for ContextBench** (its native metric); N/A for exec benches | Low — not the benchmark's verdict |
| **Setup cost** | High — Docker + images + `swebench` | Med — per-repo install recipes, build failures | **Low** — already in harness | Low |
| **Windows/WSL friendly** | Poor on Windows (Linux-only `swebench` → WSL) | Med — WSL ok, native Windows fragile | **Excellent** — pure Python, no exec | Excellent |
| **Speed** | Slow — a container per task | Med — venv build per task (cache helps) | **Fast** — no exec | Fast |
| **Trust** | High — isolated, harness-applied tests | High *if* harness applies gold `test_patch` | High — no model in the loop | **Low** — agent grades itself |
| **Coverage of CODE benches** | All 3 (overkill for ContextBench) | swe_* (best-effort) | contextbench (full); swe_* only as a weak proxy | any (unsafe) |

---

## 6. Per-benchmark recommendation (summary)

- **contextbench** → **retrieval-only, no Docker, no checkout required.** Run
  Claude Code in a memory mode and score recall/precision/F1 over the gold spans
  via the existing retrieval metrics. This is the *correct* native metric and the
  cheapest path. (Optionally also run the agentic+local-exec path for a secondary
  patch number, but it is not the benchmark's metric.)
- **swe_bench_cl** → **agentic loop + local-exec grader by default**, Docker
  opt-in. This is the benchmark that most needs the real loop **and** memory
  (continual learning over prior solved issues). Local-exec is best-effort
  (Python repos), honest `None` otherwise; Docker for a publishable headline.
- **swe_contextbench** → **agentic loop + local-exec (best-effort) default**,
  **Docker is the only faithful multi-language story** — be explicit that the
  no-Docker number is partial-coverage and not leaderboard-comparable. For a real
  cross-language resolved-rate, Docker (or `sb-cli` cloud) is required.

**Where Docker is genuinely the only faithful option:** the *resolved-rate*
(F2P/P2P) for **swe_contextbench** (multilingual) and the *headline reproducible
number* for **swe_bench_cl**. Everywhere else, no-Docker is fine or strictly
better. Do not "drop Docker" — demote it to opt-in and keep it for the numbers
that must be reproducible.

---

## 7. Phased, additive plan (with named integration points)

Everything below is **additive** and selectable; the existing blind-diff path and
Docker grader keep working and stay the conservative default until a phase flips
the per-benchmark `auto`.

**Phase 0 — offline-testable skeleton (no network, no Docker).**
- New `eval/memeval/claudecode/checkout.py`: `prepare_checkout` /
  `capture_diff` with an **injectable git runner**; the offline test points it at
  a **stub git repo fixture** (a tiny repo with one failing test + a gold
  `test_patch`) so the whole loop runs with no network.
- New `LocalExecGrader` in
  [`grader.py`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/grader.py),
  reusing
  [`resolved_from_report`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/grader.py#L65-L111);
  register in
  [`get_grader`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/grader.py#L293-L307).
- Unit tests with the existing **EchoAgent**-style fake runner (the
  `runner` is already injectable on `ClaudeCodeAgent`,
  [constructor L168](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/agent.py#L163-L179))
  + the stub repo: a fake "agent" writes the known-good fix, `capture_diff`
  returns it, `LocalExecGrader` applies it + the gold `test_patch` over the stub,
  runs `pytest`, asserts `True`; a no-op agent asserts `False`; a broken env
  asserts `None`. **No `claude`, no Docker, no network.**

**Phase 1 — agentic solve path.**
- Add `code_mode` to `ClaudeCodeAgent.__init__` and the CODE branch in
  [`solve()`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/agent.py#L202-L216);
  implement `_solve_code_agentic` (checkout → `_SYS_CODE_AGENT` prompt →
  `run_claude(cwd=checkout, permission_mode="acceptEdits", allowed_tools=None)` →
  `capture_diff` → `AgentResult(prediction=diff, patch=diff, success=None)`).
- `--code-mode {blind,agentic}` in
  [`run_bench.py`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/run_bench.py)
  (default `blind`); thread to the agent ctor at
  [L145-L146](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/run_bench.py#L145-L146).

**Phase 2 — grading defaults.**
- `--grader local` wired in
  [`_make_grader`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/run_bench.py#L68-L93);
  change `auto` per §3d (contextbench → retrieval-only; swe_* → local). Keep
  `--grader docker` and `--grader overlap` as today. Report **coverage**
  (gradable / total) alongside accuracy so partial-coverage runs read honestly
  (extend the
  [OK summary line](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/run_bench.py#L197-L198)).

**Phase 3 — memory in the loop.**
- Make `_solve_code_agentic` honor `memory_mode` (builtin / plugin / plugin-real)
  by reusing the existing seeding + recall-attribution helpers (§4), so CODE
  benches finally record `retrieve` steps and exercise the memory metrics. Add a
  test asserting a CODE plugin run produces ≥1 `record_retrieve` (closing the
  "CODE bypasses memory" gap).

**Phase 4 (optional) — real cached-checkout + WSL execution.**
- Implement the cached bare-mirror checkout strategy; document running execution
  grading from WSL on Windows. Add `sb-cli` cloud as a Docker alternative for CI.

**Relationship to `eval/memeval/native/`:** the task brief references an
in-progress `native/` package; it does **not exist in this worktree** (`main` has
no `eval/memeval/native/`). The plan above is written to live under
`eval/memeval/claudecode/` (where the agent + sandbox + CLI already are). If a
`native/` package lands, `checkout.py` + `LocalExecGrader` are the natural
contents to move there; the integration points (agent CODE branch, `run_bench`
flags, `grader` registry) are unchanged regardless of where the helpers live.

---

## 8. Honest caveats / things to verify

- **Reproducibility without Docker is genuinely worse.** Local-exec grades are
  host-dependent and partial-coverage; never present them as official
  SWE-bench(-CL/-ContextBench) numbers. The faithful resolved-rate for the
  execution benches still needs Docker (or `sb-cli`). This is the central honest
  trade-off — the recommendation is "default to the light path, keep Docker for
  the numbers that must be reproducible," not "drop Docker."
- **Multilingual SWE-ContextBench** (9 languages) is effectively un-gradeable
  without per-language toolchains; a single host can't hold them. Docker is the
  only practical faithful path there.
- **Could not verify the installed `claude` CLI supports `--max-turns`** — the
  current runner doesn't pass it
  ([`_flags`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/cli.py#L58-L73)).
  Treat the wall-clock `timeout` as the only guaranteed cap until verified
  against the installed version.
- **Checkout-by-SHA needs network** for the first task per repo; the cached-mirror
  strategy amortizes it but the very first run is online. Offline CI must use the
  stub-repo fixture path.
- **`AgentResult.success` is a footgun** for real runs (agent self-grading). The
  design deliberately returns `success=None` from real agentic runs and lets the
  harness grade; `forced_success` stays for trusted test doubles only.
- **Cost/safety of `bypassPermissions` in a real checkout**: an agent with Bash
  in a cloned repo can run arbitrary commands. The sandbox isolates *config*, not
  the filesystem/network — for untrusted repos at scale, prefer running the
  agentic loop **inside** a disposable container or WSL namespace even when
  grading is local. (Ironically, this is a reason a thin container around the
  *agent* may still be wanted even when the *grader* is Docker-free.)
- **ContextBench retrieval scoring over a real checkout** (option (c)(i))
  requires capturing which files/lines the agent read; headless `claude -p
  --output-format json` returns the final result, not a per-tool-call trace by
  default. The simpler, already-working option (c)(ii) (gold spans as seeded
  memory + the existing recall metric) avoids this and is the recommended default
  for ContextBench. Verify whether `stream-json` tool-use events expose enough to
  reconstruct read spans if (c)(i) is pursued.
