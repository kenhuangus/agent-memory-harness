---
id: ADR-harness-012
domain: harness
title: Remove harness-side memory management from the plugin-real path; accumulation is by persistent shared directory only
status: Proposed
date: 2026-06-22
contract: false
supersedes: none
superseded_by: none
owner: Keith (P1)
origin: design session 2026-06-22 (SWE-Bench-CL live-plugin pipeline); records and reverses the never-ADR'd "Fix A" group-store accumulation
---

# ADR-harness-012: Remove harness-side memory management from the plugin-real path; accumulation is by persistent shared directory only

**Status:** Proposed · **Date:** 2026-06-22 · **Contract:** no
**Supersedes:** none (records + reverses an undocumented decision — see Context)
**Superseded by:** none

## Context

The `plugin-real` path in [`eval/memeval/claudecode/agent.py`](../../eval/memeval/claudecode/agent.py)
carries an accumulation mechanism that was **never recorded as an ADR** — it lives only in code
and comments as "Fix A". For a SWE-Bench-CL group (`group_id` sequence), the harness, per task:

1. `_group_restore` — copies the accumulated `_groupstore/<group_id>` directory INTO the
   per-task `<run_dir>/.cookbook-memory`, EXCLUDING `events.jsonl`, and seeds loader priors once
   per group (sentinel-guarded), optionally via `_seed_plugin_store` (a back-door
   `memory-cli remember`);
2. drives the turn with `CLAUDE_PROJECT_DIR=<per-task run_dir/checkout>`;
3. `_drain_daydream` — waits for the async Stop-hook daydream write, with a synchronous
   `daydream-cli` backstop;
4. `_group_persist` — copies the per-task store BACK to the group store, again excluding
   `events.jsonl`.

This is harness code reading, writing, copying, excluding, and seeding files *inside the
plugin's memory store* — the black-box violation
[`ADR-eval-001`](ADR-eval-001-extract-memory-package.md) forbids, and
[`ADR-eval-003`](ADR-eval-003-pipeline-shared-memory-substrate.md) re-establishes the boundary
against. ADR-eval-003 replaces the *purpose* of all this copying — cross-task/cross-stage
accumulation — with a single persistent shared directory the plugin owns. This ADR is the
harness-domain decision to **delete** the now-dead mechanism rather than leave two paths.

The decision to make the shared directory the ONLY plugin-real model (so `memeval-bench` adopts
it too, not just the new pipeline) was taken at the 2026-06-22 design gate.

## Options considered

- **Delete the harness-side memory machinery entirely; `plugin-real` uses the persistent shared
  directory for all callers** (chosen). One boundary-correct path. `memeval-bench`'s
  continual-learning accumulation becomes directory-persistence, identical to the pipeline.
- **Keep "Fix A" as-is; add the shared-directory mode only for the pipeline.** Rejected: leaves
  the black-box-violating code in the tree, two `plugin-real` code paths to maintain, and a
  standing temptation to "just copy the store" again. The user explicitly chose one model.
- **Keep the copy machinery but stop excluding `events.jsonl`.** Rejected: the exclusion is a
  symptom; the disease is the harness touching the store at all. Half-measures keep the coupling.

## Decision

On the `plugin-real` path (BOTH the QA `_solve_plugin_real` and the agentic-CODE plugin-real
branch in `agent.py`):

- **Delete:** `_group_restore`, `_group_persist`, `_copy_store_contents`, `_plugin_group_store`,
  `_group_store_has_memory`, `_GROUP_SEEDED_SENTINEL`, `_seed_plugin_store` (the back-door
  `memory-cli remember` write), and the `events.jsonl` exclusion logic.
- **Keep `_drain_daydream` as a pure WAIT-barrier only:** block until the plugin's async
  Stop-hook daydream write has landed (the poll loop), so the next task/stage does not race the
  flock — but with NO copy-out and NO group-store persistence. The synchronous `daydream-cli`
  backstop (driving the plugin's OWN engine to *complete its write*, not to copy anything) may
  remain as part of the barrier, since it uses the plugin's surface and writes only to the
  store the plugin already owns. (Confirmed separable — the wait logic has no data dependency on
  the copy logic.)
- **Do NOT touch `_seed_plugin_store_okf`** — that is a different helper for the OKF `plugin`
  SIMULATION mode and the harness recall log, unrelated to the real plugin's store.
- **Store location:** `CLAUDE_PROJECT_DIR` is set (via `extra_env`) to the persistent shared
  directory (ADR-eval-003) for every plugin-real task; the per-task checkout remains the
  subprocess `cwd` for agentic CODE (the agent still edits the real repo there). `cwd` and
  `CLAUDE_PROJECT_DIR` are passed independently to the subprocess (verified separable).

### Verify-first probe (gates the implementation)

Because the agentic-CODE path runs `claude` with `cwd=checkout`, confirm BEFORE writing the rest
that `CLAUDE_PROJECT_DIR` passed in env **wins over cwd** when the installed plugin expands
`${CLAUDE_PROJECT_DIR}` in its committed `.mcp.json`/`hooks.json`. Probe: one plugin-real turn
with `cwd=checkout` and `extra_env CLAUDE_PROJECT_DIR=<persistent dir>`; assert the store
materializes under the persistent dir, not the checkout. **If env does not win → fall back to
symlinking `checkout/.cookbook-memory` → the persistent dir** (a boundary-adjacent compromise
noted in ADR-eval-003), and record which mechanism was used.

## Rationale

Once accumulation is "the directory persists" (ADR-eval-003), every line of the copy machinery
is dead code that also happens to violate the eval black box. Deleting it makes the boundary
structural instead of disciplinary, removes the only place the harness reasoned about the
store's on-disk layout (which backends, which files to exclude), and collapses two `plugin-real`
paths into one. Keeping the daydream wait-barrier preserves the one thing the drain was actually
needed for — not losing a task's async-written memory before the next turn — without any
file copying.

## Tradeoffs & risks

- **`memeval-bench` continual-learning behavior changes.** Prior per-group accumulation
  (`_groupstore`) is replaced by shared-directory persistence. Existing committed
  `results/v0.1/swe_bench_cl-*.json` were produced by the old mechanism and are not directly
  comparable to new runs. Accepted (cross-referenced in ADR-eval-003): the new model is correct,
  and the version bucket separates generations.
- **Per-task recall attribution.** The `events.jsonl` exclusion existed to keep recall
  attribution per task. Under one shared store, attribution must read the plugin's
  externally-observable events by session/turn id (ADR-harness-007) rather than rely on a
  per-task copied file. If insufficient, the fix is in the plugin's observable output, not in
  harness copying. Called out as an open item in ADR-eval-003.
- **Tests lock the deleted behavior.** `eval/tests/test_plugin_group_store.py` asserts the copy
  semantics (carry-across-tasks, events-stays-per-task, seed-once-per-group,
  ungrouped-no-group-store, copy-excludes-events). Those tests are rewritten to assert the new
  no-copy / shared-directory behavior; `test_drain_is_noop_under_fake_runner` (the wait-barrier
  guard) is KEPT.
- **Dependence on the env-over-cwd probe.** If the probe fails, the symlink fallback is a small
  boundary compromise (the harness names `.cookbook-memory`). Mitigated by trying the clean
  env-var path first and recording the mechanism used.

## Consequences for the build

- **Policy:** no eval/harness code path reads, writes, copies, moves, excludes, prunes, or seeds
  any file inside the plugin's memory store on the `plugin-real` path. The harness's only store
  interaction is `mkdir -p` of the version directory (ADR-eval-003) and setting
  `CLAUDE_PROJECT_DIR`.
- **Policy:** `_drain_daydream` is a wait-barrier; it must not copy or persist store contents.
- **Enforcement:** a boundary unit test plus a review grep guard assert the deleted helpers and
  patterns (`_copy_store_contents`, `copytree` of `.cookbook-memory`, `events.jsonl` copy/exclude)
  are absent on the `plugin-real` path.
- **Affected files:**
  - `eval/memeval/claudecode/agent.py` — delete the helpers above; reduce `_drain_daydream`;
    set `CLAUDE_PROJECT_DIR` to the shared dir in both plugin-real branches.
  - `eval/memeval/claudecode/run_bench.py` — pass the persistent dir through; no per-group copy.
  - `eval/tests/test_plugin_group_store.py` — rewrite for no-copy / shared-directory behavior;
    keep the drain-noop test.
