---
id: ADR-eval-003
domain: eval
title: The eval pipeline owns only the memory store DIRECTORY; ONE shared substrate per pipeline version, touched only by the plugin
status: Proposed
date: 2026-06-22
contract: true
supersedes: none
superseded_by: none
owner: Ken (P2) — eval↔memory boundary; team sign-off required ([CONTRACT])
origin: design session 2026-06-22 (SWE-Bench-CL live-plugin pipeline)
---

# ADR-eval-003: The eval pipeline owns only the memory store DIRECTORY; ONE shared substrate per pipeline version, touched only by the plugin

**Status:** Proposed · **Date:** 2026-06-22 · **Contract:** yes
**Supersedes:** none · **Superseded by:** none

## Context

We are building a 5-stage SWE-Bench-CL pipeline that runs the *real* shipping plugin
(`plugin-real`, native install) end to end: (1) a no-plugin base, (2) plugin with blank
memory, (3) plugin with the memory accumulated in stage 2, (4) a Dream consolidation pass,
(5) plugin with the dream-pruned memory. The whole experiment tests one hypothesis — that a
single accumulating memory substrate makes the agent get smarter over the tasks it sees.

The existing `plugin-real` path in `eval/memeval/claudecode/agent.py` accumulates memory by
**harness-managed copying**: per task it copies a `_groupstore/<group_id>` directory INTO a
per-task `<run_dir>/.cookbook-memory` (the "Fix A" group store), excludes `events.jsonl` on
the way, drives the turn, drains the async daydream, then copies the store back out. That
makes the eval harness read, write, copy, and exclude files *inside* the plugin's memory —
the exact black-box violation [`ADR-eval-001`](ADR-eval-001-extract-memory-package.md)
exists to prevent. [`ADR-eval-001`](ADR-eval-001-extract-memory-package.md) §"Consequences"
already names the only legitimate seam: **`$MEMORY_STORE` (a path) plus the plugin's
externally-observable outputs**. [`ADR-dreaming-019`](ADR-dreaming-019-memory-store-is-a-directory.md)
made `$MEMORY_STORE` a directory and its "Open items" explicitly defers, to a cross-domain
conversation with Brent (storage) and Ken (eval), the question *"should the bench default to
a checked-in / shared store directory so two runs see the same memory state?"* This ADR
answers that question for the pipeline and re-establishes the boundary for `plugin-real`.

The store is resolved by the plugin's OWN committed config:
[`.mcp.json`](../../plugin/marketplace/cookbook-memory/.mcp.json) (recall) and
[`hooks.json`](../../plugin/marketplace/cookbook-memory/hooks/hooks.json) (SessionStart /
UserPromptSubmit / Stop / PreCompact — daydream) both set
`MEMORY_STORE=${CLAUDE_PROJECT_DIR}/.cookbook-memory`. So a single env var the harness sets —
`CLAUDE_PROJECT_DIR` — fully determines where the plugin reads, writes, and dreams. The
harness need not (and per the boundary, must not) name `MEMORY_STORE` or `.cookbook-memory`.

## Options considered

- **Harness owns only the store DIRECTORY; ONE shared substrate per pipeline version; the
  plugin owns everything inside** (chosen). The harness `mkdir -p`s `results/v{tag}/_memory/`
  once, sets `CLAUDE_PROJECT_DIR` to it for every plugin-real task across every stage, and
  never reads/writes/copies/prunes anything inside. Accumulation across stages 2→3→5 happens
  because the *directory persists*, not because the harness moves files.
- **Keep harness-managed group-store copying, scope it per sequence.** Rejected: it is the
  black-box violation itself; couples eval to the plugin's on-disk layout (which backends
  exist, which files to exclude); and the `events.jsonl` exclusion is the harness reasoning
  about plugin internals. Every future store-layout change in storage/dreaming would break
  eval.
- **Per-sequence or per-task store scoping** (a fresh substrate per SWE-Bench-CL sequence).
  Rejected: it contradicts the experiment. The hypothesis is ONE accumulating memory across
  everything the agent sees; scoping per sequence/task throws away the cross-sequence transfer
  signal and reintroduces harness bookkeeping about which store belongs to which work.
- **Symlink each per-task `.cookbook-memory` to the shared dir.** Considered as the fallback
  if `CLAUDE_PROJECT_DIR` does not override cwd (see ADR-harness-012's verify-first probe).
  Workable but the harness creating a symlink *named* `.cookbook-memory` is boundary-adjacent
  (it names the plugin's path), so it is the fallback, not the default.

## Decision

For the pipeline (and, per [`ADR-harness-012`](ADR-harness-012-remove-harness-memory-management.md),
for `plugin-real` generally), the eval harness's ENTIRE relationship to memory is:

1. **Ensure ONE directory exists and persists:** `results/v{tag}/_memory/` — one shared
   substrate per pipeline **version** (the git tag on `main`, per
   [`ADR-eval-004`](ADR-eval-004-pipeline-version-from-git-tag.md)), NOT scoped per sequence,
   domain, or task. The harness only ever calls `mkdir(parents=True, exist_ok=True)` on it.
2. **Point the plugin at it:** set `CLAUDE_PROJECT_DIR=<that directory>` in the subprocess env
   for every plugin-real turn. The plugin resolves `${CLAUDE_PROJECT_DIR}/.cookbook-memory`
   itself, through its router, for recall, write, and daydream.
3. **Touch nothing inside.** The harness performs no read, write, copy, move, exclude, prune,
   or symlink of any file under the store. The Dream stage is triggered only through the
   plugin's own surface (`daydream-cli dream`), never by the harness calling the worker or
   reading the store. Recall *attribution* may read the plugin's externally-observable
   `events.jsonl` (an output, per ADR-eval-001), but never copies or mutates it.

The store is the plugin's private artifact. The harness owns the **place**, the plugin owns
the **contents**.

## Rationale

The black-box boundary is only real if the eval engine *cannot* reach into memory — exactly
the principle [`ADR-eval-001`](ADR-eval-001-extract-memory-package.md) established for imports,
applied here to the filesystem. Because the plugin's committed config already maps
`CLAUDE_PROJECT_DIR` → its store, **one env var** is the whole seam: no harness code needs to
know the store has a `memory.db`, a `markdown/`, a `graph.db`, an `events.jsonl`, or how those
evolve. Accumulation-by-persistence is the simplest possible mechanism — a directory that
isn't deleted between stages — and it is the honest model of the hypothesis: one memory that
grows. Deleting the copy machinery (ADR-harness-012) removes the only place the harness
reasoned about plugin internals, so the boundary becomes structural, not disciplinary.

## Tradeoffs & risks

- **Loss of per-task store isolation for attribution.** Today the per-task copy + `events.jsonl`
  exclusion gives each task its own recall-attribution slice. With one shared substrate,
  attribution must come from the plugin's own per-session/per-turn events, read (not copied)
  as an observable output. Mitigation: the plugin already writes a structured events stream
  ([`ADR-harness-007`](ADR-harness-007-memory-events-stream.md)); attribution reads it by
  session/turn id. If that proves insufficient, the fix belongs in the plugin's observable
  output, not in harness-side copying.
- **Cross-run contamination if the dir isn't version-scoped.** Because the substrate persists,
  a stale `_memory/` from a different code version would silently pollute results. Mitigated by
  scoping strictly to the version directory (ADR-eval-004): a new tag ⇒ a new empty substrate.
- **Reproducibility is unlocked, not solved.** A shared persistent dir means re-running stage 3
  twice starts from different memory the second time (it accumulated). That is intended for the
  pipeline (it models "over time"), but means a single stage is not independently reproducible
  without resetting the substrate. The pipeline always runs the stages in order from a known
  start; ad-hoc single-stage reruns must reset `_memory/` themselves.
- **Depends on `CLAUDE_PROJECT_DIR` overriding cwd** for the installed plugin's `${…}`
  expansion. If it does not, the symlink fallback applies (a boundary-adjacent compromise).
  Resolved by the verify-first probe in
  [`ADR-harness-012`](ADR-harness-012-remove-harness-memory-management.md).
- **`memeval-bench`'s continual-learning behavior changes.** Its prior per-group accumulation
  is replaced by shared-directory persistence (ADR-harness-012). Existing committed results
  under `results/v0.1/` were produced by the old mechanism; new runs are not directly
  comparable to them. Accepted: the new model is the correct one and the version bucket
  separates generations.

## Consequences for the build

- **Policy — the harness owns the directory, never the contents.** No eval code path may read,
  write, copy, move, exclude, prune, or symlink any file under the memory store on the
  `plugin-real` path. Enforced by [`ADR-harness-012`](ADR-harness-012-remove-harness-memory-management.md)
  (which deletes the offending helpers) plus a boundary unit test + a grep guard in review
  (`_copy_store_contents`, `copytree`, `.cookbook-memory` file reads, `events.jsonl` copies →
  must be absent on the pipeline path).
- **Policy — ONE substrate per version, not per work-unit.** The store directory key is the
  pipeline version only. Sequence/task identity is NOT encoded in the store path and is NOT
  recorded in memory; it is recorded in the run metadata (see contract shape below), because
  the substrate can no longer tell you which sequence produced it.
- **Policy — Dream via the plugin surface only.** Stage 4 shells `daydream-cli dream` with
  `CLAUDE_PROJECT_DIR`/`MEMORY_STORE` pointed at the substrate; it never imports or calls the
  dreaming worker directly and never reads the store.

- **Contract — source of truth:** the pipeline orchestrator
  `eval/memeval/claudecode/pipeline.py` (the only writer of the store *directory*) and the
  `plugin-real` branch of `eval/memeval/claudecode/agent.py` (the only setter of
  `CLAUDE_PROJECT_DIR`). The plugin's committed `.mcp.json` + `hooks.json`
  (`MEMORY_STORE=${CLAUDE_PROJECT_DIR}/.cookbook-memory`) are the binding the contract relies on.

- **Contract — shape:** the harness↔memory seam is exactly one environment variable:

  ```
  CLAUDE_PROJECT_DIR = <results/v{version}/_memory>      # set by the harness
  # plugin (its own config) derives, and the harness MUST NOT set or read:
  MEMORY_STORE       = ${CLAUDE_PROJECT_DIR}/.cookbook-memory
  ```

  Run metadata MUST carry what the substrate no longer can (it is version-keyed, not
  work-keyed) — the per-run `pipeline` metadata block records at minimum:
  `{version, git_sha, sequence, limit, n_tasks, model, code_mode, grader, dream:{provider,
  model}, memory_store, stages[], started_at, ended_at}`. `sequence` is mandatory: with one
  shared substrate the Y-domain is otherwise unrecoverable from results.

- **Exhaustive consumers** (must set/read only the one env var and honor the no-touch policy):
  - `eval/memeval/claudecode/pipeline.py` — creates the version dir, sets `CLAUDE_PROJECT_DIR`,
    writes results + the `pipeline` metadata block.
  - `eval/memeval/claudecode/agent.py` (`_solve_plugin_real` + the agentic-CODE plugin-real
    branch) — sets `CLAUDE_PROJECT_DIR` to the shared dir; performs no store copying.
  - `eval/memeval/claudecode/run_bench.py` — `memeval-bench` now passes the persistent dir
    through (no per-group copy).
  - `eval/memeval/dreaming/cli.py` (`daydream-cli dream`) — the only Dream trigger surface.
  - The plugin's `.mcp.json` + `hooks.json` — the `${CLAUDE_PROJECT_DIR}` binding (unchanged).

- **`architecture.md` consequence:** §7 is extended (new §7.4) to state the shared-per-version
  substrate and the harness-owns-the-directory-only rule. Lands in the same `[CONTRACT]` PR.

## Open items

- **Attribution fidelity** under a shared substrate (per-turn recall slices) — confirm the
  events stream is sufficient during the verify-first probe; if not, the enhancement is in the
  plugin's observable output, owned by harness/storage, not eval.
- **Native CL metrics** (ACC/F/BWT/FWT/AULC/CL-Score) per stage are captured in v1 (no
  deferral) via `memeval.native`; their relationship to the shared-substrate accumulation
  (the paper assumes per-sequence resets) is documented in the pipeline summary.
