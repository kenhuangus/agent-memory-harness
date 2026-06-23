# Feature: Pipeline Memory Observability

**Status:** Done · **Date:** 2026-06-23

## What this delivers (before -> after)

Before, a pipeline run could finish with all-zero metrics while hiding whether recall was attempted, whether hits were returned, whether memories were written, or whether CODE tasks were actually graded.

After, pipeline artifacts and summaries expose memory health and grading coverage, and the harness flags invalid memory-lift stages when the shared store never accumulates.

## Requirements & acceptance criteria

1. Given a plugin-real stage with recall events, when the pipeline records results, then it reports recall attempts separately from recall-with-hits.
2. Given a plugin-real stage whose store is empty after completion, when results and summaries are written, then the stage is flagged with zero durable memory items rather than treated as a valid memory-lift stage.
3. Given CODE tasks run with `grader=none`, when the summary is rendered, then accuracy displays as ungraded instead of implying real failures.
4. Given the dream stage runs, when pipeline metadata is written, then the actual dream worker result is preserved and surfaced in the summary.
5. Given the pipeline starts or finishes a stage, when memory preflight or post-stage invariants fail, then the artifact includes structured warnings without aborting an otherwise useful plumbing run.

## Approach

The change follows the existing pipeline result pattern: derive stage rows from `RunResult`, enrich rows through the `extra` channel, and keep plugin-real store ownership inside the plugin. The harness reads the plugin's event stream and durable store counts for observability only; it does not seed, copy, or mutate the store.

No new dependency is needed. SQLite row counts use the standard library `sqlite3`, and summary rendering stays in `pipeline_summary.py`.

## Build plan

- [x] Add store/event health helpers and tests for recall attempts, recall hits, write completion, and durable item counts.
- [x] Enrich pipeline stage rows with memory health, grading coverage, and warnings.
- [x] Render memory health and ungraded accuracy honestly in markdown and JSON summaries.
- [x] Add preflight/post-stage validation warnings for missing MCP recall and non-accumulating stores.
- [x] Run impacted tests, then the eval test slice.

## Quality bars

Security: no secrets are logged; only aggregate counts and store paths already present in pipeline metadata are recorded.

Non-functional: counting must be cheap and bounded to the shared store's event file plus SQLite row counts.

Observability: warnings must be machine-readable in JSON and visible in markdown.

## Decisions, assumptions & blockers

Decisions made:

- Store inspection is read-only and limited to event counts plus durable row counts, preserving the plugin-owned store boundary.
- Recall attempts and recall hits are separate counters; zero-hit recalls still prove the MCP path was reached.
- Pipeline preflight is non-blocking. It records warnings because plumbing runs can still be useful even when memory-lift interpretation is invalid.
- CODE accuracy renders as unavailable when no tasks were graded, while the raw metric remains backward-compatible in JSON.

Assumptions:

- SQLite `memory.db.items` and `graph.db.nodes`, plus top-level markdown files, are sufficient durable-store signals for current plugin backends.
- The synthetic preflight should use an isolated temporary store rather than writing sentinel data into the benchmark substrate.

Deferred / blockers:

- None.
