# Feature: 5-stage SWE-Bench-CL eval pipeline driven by the live plugin

**Status:** ADRs in review (PR #97) — build begins after sign-off · **Date:** 2026-06-22 · **Owner-of-record:** Ken (eval), with harness/storage/dreaming ADRs by their domain owners

> **Dataset context:** SWE-Bench-CL reorganizes SWE-bench Verified into **8 per-repo sequences**
> (the 8 "domains"), 273 tasks total, strictly ordered within each sequence (sizes 19–50). A
> pipeline run takes **X tasks from one sequence Y** — `--limit X` tasks of `--sequence Y`, by
> `Task.order`. The 8 sequence ids: `django_django` (50), `sympy_sympy` (50), `sphinx-doc_sphinx`
> (44), `matplotlib_matplotlib` (34), `scikit-learn_scikit-learn` (32), `astropy_astropy` (22),
> `pydata_xarray` (22), `pytest-dev_pytest` (19) — each suffixed `_sequence`.

### Locked at the gate (2026-06-22)
1. **Shared persistent dir is the ONLY plugin-real memory model.** `memeval-bench` adopts it too —
   its continual-learning accumulation now happens by directory persistence, not harness copy. The
   group-store copy machinery is removed everywhere (one boundary-correct path).
2. **ADR-eval-003 is `contract: true`** → full `[CONTRACT]` PR, and `architecture.md` §7 is extended
   to state the shared-per-version substrate + the harness-never-touches-memory rule.
3. **Defaults:** sequence `pytest-dev_pytest_sequence` (19 tasks), `--limit 20` (whole sequence).

> Working plan distilled from the user-reviewed plan at
> `~/.claude/plans/ok-the-next-goal-sparkling-valiant.md`. This doc is the single feature artifact.

## What this delivers (before → after)

- **Before:** `memeval-bench` runs builtin-vs-plugin side-by-side for one benchmark; there is no
  end-to-end "does accumulated + dreamed memory help over time" experiment, no cross-run shared
  memory substrate, no git-tag-versioned pipeline, no cross-stage summary.
- **After:** one wrapper command runs 5 ordered stages over the same X tasks of a chosen
  SWE-Bench-CL sequence — (1) base/no-plugin, (2) plugin/blank memory, (3) plugin/accumulated,
  (4) dream-consolidate, (5) plugin/dreamed — all sharing ONE persistent version-scoped memory
  substrate owned entirely by the plugin, and emits a summary comparing base → final.

## Requirements & acceptance criteria

R1. One wrapper command runs all 5 stages in order over the same X tasks (`--limit`) of one
   named sequence (`--sequence`, by `Task.order`).
R2. The eval harness NEVER reads/writes/copies/prunes/inspects plugin memory — it only ensures the
   version-scoped store directory exists and persists. **Once the harness has set the directory up
   and pointed `CLAUDE_PROJECT_DIR` at it, ONLY the plugin ever touches it.**
R3. Memory is ONE shared substrate per pipeline version (`results/v{tag}/_memory/`), not scoped per
   sequence/domain/task; it accumulates across stages 2→3→5 purely by directory persistence, so the
   theory under test — a single accumulating memory that makes the agent smarter over time — is what
   actually runs. Stage 3 writes NEW memories **alongside** the memories stage 2 accumulated (same
   substrate, additive); stage 5 runs on that substrate after the dream pass.
R4. Stages 2/3/5 run the real plugin (`plugin-real`, native install), agentic CODE mode, graded by
   local test execution (`LocalExecGrader`).
R5. The dream stage (4) is a NO-OP placeholder with no side effects — whole-store consolidation
   is not implemented yet (ADR-dreaming-020). It records `status: not-implemented` so the 5-stage
   shape + base→final comparison stand; when real consolidation lands it is invoked ONLY through the
   plugin's own surface. Stage 5 runs on the same substrate stage 3 left (delta ~0 today).
R6. Pipeline version = git tag on the commit; on an untagged commit it falls back to the
   (sanitized) **branch name** (`vbranch-<branch>`), then to `MEMORY_VERSION` (detached HEAD /
   no git). So local iteration on a feature branch keys the substrate by that branch.
R7. The wrapper is interactive by default (offer/override defaults, confirm) with a non-interactive
   `--yes` mode taking flags/defaults.
R8. A summary file tabulates per-stage metrics and base→final deltas.

**Acceptance criteria (Given/When/Then):**

- AC1 (R2/R3) — Given a pipeline run on the offline/fake-runner path, When stages 2 and 3 execute,
  Then every plugin-real task receives the SAME `CLAUDE_PROJECT_DIR` (the one `_memory/` dir) and the
  harness performs NO `_copy_store_contents`/`copytree`/`events.jsonl` read on the plugin-real path
  (asserted by a boundary unit test + grep guard).
- AC2 (R3) — Given stage 2 has written memory, When stage 3 runs, Then a stage-3 turn can recall a
  memory created in stage 2 with no harness copy (the dir simply persisted) — evidenced by the
  plugin's own `events.jsonl` recall hits referencing prior-stage memory.
- AC3 (R4) — Given a CODE task, When a plugin-real stage runs in agentic mode, Then `cwd` is the
  per-task checkout AND `CLAUDE_PROJECT_DIR` is the persistent `_memory/` dir, and grading is via
  `LocalExecGrader` (resolve rate), degrading to ungraded (not crash) when the env can't build.
- AC4 (R5) — Given stage 4, When it runs, Then it shells `daydream-cli dream` against the persistent
  dir and records the emitted summary under a `dream` key; the harness makes no direct `worker.dream`
  call and reads no store files.
- AC5 (R6) — Given a tagged `main` commit, When the pipeline runs, Then output lands under
  `results/v{tag}/`; Given an untagged commit, Then it falls back to `MEMORY_VERSION` gracefully.
- AC6 (R7) — Given no args, When invoked, Then it prompts with defaults and a final confirm; Given
  `--yes`, Then it runs non-interactively from flags/defaults with no prompts.
- AC7 (R8) — Given a completed run, Then `SUMMARY-swe_bench_cl-{stamp}.md` (+ `.json`) lists
  accuracy/relevancy/recency/efficiency/n/cost for stages 1/2/3/5, the stage-4 dream summary, and
  base→final deltas.
- AC8 — `make test` (offline, stdlib-only) stays green.

## Approach

**Reuse:** `run_bench.py`'s grader/limit/select helpers, `agent.py::run_agent` + `_select_group_aware`,
`results.py::write_benchmark_results`/`run_timestamp`/`normalize_version`, `grader.py::LocalExecGrader`,
`dreaming/cli.py` (`daydream-cli dream` — the plugin's surface), `sandbox.py::setup_real_plugin`.

**New:** `eval/memeval/claudecode/pipeline.py` (the orchestrator + `memeval-pipeline` console script,
interactive + `--yes`); a git-tag version resolver in `eval/memeval/results.py` (Ken owns it).

**Core seam (the load-bearing change):** add a persistent shared-project-dir mode to `ClaudeCodeAgent`.
When set, plugin-real (both QA and agentic-CODE branches) sets `CLAUDE_PROJECT_DIR` = the one
persistent dir for EVERY task (via `extra_env`, with `cwd` still the per-task checkout — verified
separable), and the harness-side memory machinery is **removed**:
`_group_restore`/`_group_persist`/`_copy_store_contents`/`_plugin_group_store`/`_group_store_has_memory`/
`_GROUP_SEEDED_SENTINEL`/`_seed_plugin_store` and the `events.jsonl` exclusion all go; `_drain_daydream`
is reduced to its pure wait-barrier (the poll loop, lines ~882-891, which is cleanly separable per
grounding) — KEEP that. `_seed_plugin_store_okf` (OKF `plugin` sim, unrelated) is NOT touched.

**Store resolution (verified):** the plugin's own committed `.mcp.json` + `hooks.json` bind
`MEMORY_STORE=${CLAUDE_PROJECT_DIR}/.cookbook-memory` for recall AND daydream, and `_clean_env` merges
`extra_env` (incl. `CLAUDE_PROJECT_DIR`) into the subprocess env independently of `cwd`. So the harness
sets exactly ONE var. **Verify-first probe:** confirm `CLAUDE_PROJECT_DIR` (env) wins over `cwd` for
the installed plugin's `${CLAUDE_PROJECT_DIR}` expansion before building the rest; fallback = symlink
`checkout/.cookbook-memory` → persistent dir (boundary caveat noted).

**Prerequisite (storage domain):** `plugin/cookbook_memory/core/contract.py` builds the graph backend
in-memory (`GraphStore()`) → evaporates on exit; change to `GraphStore(path=str(root / "graph.db"))`
so the graph layer persists across stages alongside `memory.db`/`markdown/`. `GraphStore.__init__`
already accepts `path=`; `root` derives from `$MEMORY_STORE`.

**Stage wiring** (all reuse `run_agent` + `LocalExecGrader`; stages 2/3/5 share the one `_memory/` dir):

| Stage | mode | store | results row |
|------|------|-------|-------------|
| 1 base | off | none | base |
| 2 blank | plugin-real | persistent dir, empty | plugin-blank |
| 3 accum | plugin-real | same dir (holds stage-2 mem) | plugin-accum |
| 4 dream | — | `daydream-cli dream` over same dir | dream summary |
| 5 final | plugin-real | same dir (post-dream) | plugin-dreamed |

## ADR slate (the reason for this gate)

Per `docs/adrs/README.md` numbering (eval→003, harness→012, storage→002, dreaming→021):

- **ADR-eval-003 (contract: true)** — *Eval ↔ plugin memory trust boundary for the live pipeline + the
  shared per-version substrate.* The harness only guarantees the version-scoped store DIR exists and
  persists; the plugin owns all reads/writes via `${CLAUDE_PROJECT_DIR}/.cookbook-memory`. ONE substrate
  per pipeline version, not per-sequence/task. Filed eval (it governs the eval black-box boundary).
  Aligns with ADR-eval-001, ADR-storage-001, ADR-dreaming-019. Consumers: `pipeline.py`,
  `ClaudeCodeAgent` plugin-real path.
- **ADR-eval-004** — *Pipeline version = git tag on `main`* (fallback `MEMORY_VERSION`); output bucket
  `results/v{tag}/`. Convention other runs/result files build against.
- **ADR-harness-012** — *Remove harness-side memory management from the plugin-real path* (records the
  never-ADR'd "Fix A" group-store accumulation and reverses it): the persistent shared dir + plugin
  ownership replaces harness copy-in/copy-out; `_drain_daydream` kept as a pure wait-barrier.
- **ADR-storage-002** — *Persist the graph backend* (`GraphStore(path=…)`), so all three backends live
  under `$MEMORY_STORE` and accumulate across stages. Small but load-bearing for the substrate theory.
- **Dreaming:** likely NO new ADR — stage 4 uses the existing `daydream-cli dream` surface
  (ADR-dreaming-002/020) unchanged; the detection-only/mutation-gated status is already ADR'd
  (ADR-dreaming-020). Confirm at the gate.

## Results & run metadata (the comparison contract)

**Reuse the existing machinery — do not invent a parallel schema.** Each stage already produces a
`RunResult` (via `run_agent`); `result_record()` flattens it to a ledger row and `write_benchmark_results()`
writes the per-benchmark versioned file (`{schema, memory_version, benchmark, timestamp, runs[]}`).
The pipeline writes ONE such file holding all stage rows + a `dream` block + a top-level `pipeline`
metadata block, and a derived `SUMMARY` for human comparison.

**Per-stage row** = the standard `result_record()` output (already carries: `metrics`
{accuracy=LocalExecGrader resolve rate, recency, relevancy, efficiency, n}, `n_tasks`,
`entries_available`, `limit`, `selection`, `cost_usd`, `tokens_in/out`, `partial`, `budget_exceeded`,
`source`, `reliability`{n_errors, memory_reached, errors}, `mode`, `label`). Stamp each row's
stage identity + provenance via the existing `extra=` channel (merged at row top-level):
- `pipeline_stage`: `base | plugin-blank | plugin-accum | plugin-dreamed`
- `stage_index`: 1 | 2 | 3 | 5
- `git_sha`, `git_tag` (the version)

**Run metadata block** (NEW top-level key in the per-benchmark file — because the sequence is no
longer recorded in memory, it MUST be captured here). One `pipeline` object per run:
```json
"pipeline": {
  "version": "v0.1.2",            // git tag on main (ADR-eval-004), or MEMORY_VERSION fallback
  "git_sha": "d725858",
  "sequence": "pytest-dev_pytest_sequence",   // the Y domain — NOT in memory anymore, so recorded here
  "limit": 20, "n_tasks": 20, "n_stages": 5, "n_eval_stages": 4,   // number of runs in this pipeline
  "model": "claude-haiku-4-5",
  "code_mode": "agentic", "grader": "local", "plugin_workers": 1, "budget_usd": 20,
  "dream": { "provider": "openrouter", "model": "inclusionai/ling-2.6-flash" },  // from DREAM_PROVIDER/DREAM_MODEL (ADR-dreaming-004); records the dreamer version
  "memory_store": "results/v0.1.2/_memory/",   // the shared substrate path (for provenance, not for the harness to read)
  "stages": ["base","plugin-blank","plugin-accum","dream","plugin-dreamed"],
  "started_at": <epoch>, "ended_at": <epoch>
}
```
The dreamer model is read at runtime from `DREAM_MODEL`/`DREAM_PROVIDER` (defaults
`inclusionai/ling-2.6-flash` / `openrouter`). Git tag/sha via the version resolver (step 3).

**Dream block** = the summary dict emitted by `daydream-cli dream` (counts: total_items,
duplicate_clusters, items_in_duplicates; jobs_run/skipped_jobs), under a `dream` key — plus a
`dream_consolidation: "detection-only (WIP)"` flag so the stage-3→5 delta is read correctly.

**SUMMARY file** (`SUMMARY-swe_bench_cl-{stamp}.md` + `.json`): a table of the 4 eval stages
(accuracy/relevancy/recency/efficiency/n/cost) + the dream block, with explicit deltas:
base→plugin-blank, plugin-blank→plugin-accum, plugin-accum→plugin-dreamed, and headline base→final.
The `.json` sibling carries the same numbers machine-readably (reuse, don't reformat, the metric dicts).

**Native CL metrics (IN v1 — no deferral):** SWE-Bench-CL has a paper-native report
(ACC/F/BWT/FWT/AULC/CL-Score via `native/spec.py`). Each plugin-real eval stage captures BOTH the
standard `RunResult` metrics (resolve-rate accuracy is the headline, in the ledger rows) AND the
paper-native CL report, written under a `native` block per stage and surfaced in the SUMMARY so the
continual-learning story (forgetting / backward+forward transfer / AULC) is comparable base→final.

## Build plan

> **Process (locked):** ALL ADRs are written FIRST, committed to the feature branch, and reviewed by
> the user BEFORE any code. No deferred work. Code steps (0-6) begin only after ADR sign-off.

- [x] **A. ADRs (FIRST, review gate)** — full slate written, index rows added, `architecture.md` §7.4
  reconciled; committed to `eval/swe-bench-cl-pipeline`; opened as **draft PR #97** (ADRs only).
  **Awaiting user/owner review before any code.**
- [ ] **0. Verify-first probe** — confirm `CLAUDE_PROJECT_DIR`(env) wins over `cwd` for the installed
  plugin's `MEMORY_STORE` expansion (one plugin-real turn; assert store materializes under the
  persistent dir, not the checkout). Decide env-var vs symlink. (AC3)
- [ ] **1. Storage prerequisite** — `contract.py`: `GraphStore(path=str(root/"graph.db"))`; add a test
  that `graph.db` appears under the store dir after a write. (ADR-storage-002)
- [ ] **2. Agent seam** — add persistent-project-dir mode to `ClaudeCodeAgent`; set `CLAUDE_PROJECT_DIR`
  to it for both plugin-real branches; DELETE the harness-side memory machinery; reduce
  `_drain_daydream` to the wait-barrier. Rewrite `test_plugin_group_store.py` to assert the new
  no-copy/shared-dir behavior (keep `test_drain_is_noop_under_fake_runner`). (AC1, ADR-harness-012)
- [ ] **3. Version resolver** — `results.py::resolve_pipeline_version()` (git describe → fallback). (AC5)
- [ ] **4. Orchestrator** — `pipeline.py`: 5-stage sequence, shared `_memory/` dir, interactive+`--yes`,
  incremental results file, `daydream-cli dream` stage. Exposed as the `memeval-pipeline` console
  script (no `make` wrapper — it takes flags, which `make` forwards poorly). (R1, AC4, AC6)
- [ ] **5. Results + summary** — write the per-benchmark file with all stage rows (stage identity +
  git provenance via `extra=`), the top-level `pipeline` metadata block (sequence, model, dreamer
  model/provider, version, limit, stages, timestamps), the `dream` block, and the derived
  `SUMMARY-*.md`/`.json` with per-stage metrics + base→final deltas. (AC7, R8)
- [ ] **6. Offline smoke + boundary tests** — end-to-end fake-runner run on the vendored fixture,
  `--limit 3`; boundary assertion test (no copy/events touch); accumulation-by-persistence check
  (stage-3 recalls a stage-2 memory with no harness copy); `make test` green. (AC1, AC2, AC8)
- [ ] **7. Live verification** — tiny live run `memeval-pipeline --sequence pytest-dev_pytest_sequence
  --limit 3 --model claude-haiku-4-5 --grader local --budget-usd 5`: all 5 stages run, local-exec
  grading produces real resolve rates, the summary shows base→final, and `graph.db` appears in the
  store dir. Sandbox: `setup_real_plugin` into `eval/.claude-sandbox` (one-time `/login` on macOS).

## Quality bars

- **Trust boundary (the headline):** enforced by deletion + a boundary test + a grep guard — the
  harness has no code path that reads/writes/copies plugin memory on the pipeline path. (R2)
- **Fail-open preserved:** `LocalExecGrader` degrades to ungraded (not crash); `daydream-cli` fail-opens
  without `OPENROUTER_API_KEY` (ADR-dreaming-012) — advisory, non-blocking.
- **Cost/non-functional:** agentic SWE-Bench-CL runs are slow/$$ — default a small sequence + small
  `--limit` + hard `--budget-usd`; plugin stages at `--plugin-workers 1` (MCP concurrency limit).
- **Observability:** recall attribution stays per-stage via the plugin's own `events.jsonl`
  (harness reads it for attribution only, never copies/mutates).
- **n/a:** no new network surface, no auth change (subscription-only, API keys stripped — existing).

## Decisions, assumptions & blockers

**Decisions locked at the gate (all prior "open" items resolved):**
- Shared persistent dir is the ONLY plugin-real memory model; `memeval-bench` adopts it too (its
  per-task/group accumulation is replaced by directory persistence). One boundary-correct path.
- ADR-eval-003 is `contract: true` → `[CONTRACT]` PR; `architecture.md` §7.4 added.
- No new dreaming ADR — stage 4 uses the existing `daydream-cli dream` surface (ADR-dreaming-002/020);
  detection-only/mutation-gated status already ADR'd (ADR-dreaming-020).
- Defaults: `--sequence pytest-dev_pytest_sequence` (19 tasks), `--limit 20` (whole sequence).
- `CLAUDE_PROJECT_DIR`(env) = persistent dir while `cwd` = per-task checkout; gated by the
  verify-first probe (fallback: symlink).
- Native CL metrics are IN v1 (no deferral).

**Assumptions (to confirm during build, correctable):**
- The plugin's native daydream writes learned memory into `${CLAUDE_PROJECT_DIR}/.cookbook-memory`
  such that a later stage recalls earlier-stage memory with no harness copy (load-bearing — step 6
  accumulation check verifies it).
- `CLAUDE_PROJECT_DIR` passed in env wins over `cwd` for the plugin's `${…}` expansion (step 0 probe).

**Open items carried forward (from ADR-eval-003, not blockers for v1):**
- Per-turn recall attribution under one shared store (reads the events stream, never copies).
- Native CL metrics assume per-sequence resets; their reading against an accumulating substrate is
  documented in the summary rather than "corrected."

**Blockers:** none. `OPENROUTER_API_KEY` unset only weakens the dream/accumulation lift (advisory,
fail-open — ADR-dreaming-012), it does not block the run.
