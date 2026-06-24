---
id: ADR-eval-005
domain: eval
title: Surface per-task grade reasons in the run output by propagating the grader's last_reason
status: Accepted
date: 2026-06-23
contract: false
supersedes: none
superseded_by: none
owner: Ken (P2)
origin: results/vbranch-main-b28b7af6 analysis — accuracy floored at 0 with graded_n=1/3
---

# ADR-eval-005: Surface per-task grade reasons in the run output by propagating the grader's last_reason

**Status:** Accepted · **Date:** 2026-06-23 · **Contract:** false
**Supersedes:** none · **Superseded by:** none

## Context

[ADR-eval-002](ADR-eval-002-docker-free-code-grading.md) made `LocalExecGrader`
**degrade to `None` (UNGRADED)** on any environment problem — bad checkout, patch
won't apply, gold tests won't apply, venv/test-run failure — so accuracy never
counts a fake `False`. That honesty rule is correct and stays.

Its blind spot surfaced in the `swe_bench_cl` pipeline run `20260623T223036Z`: every
stage reported **`accuracy = 0.0000` with `graded_n = 1`** of 3 tasks. The saved
results could not answer the first question anyone asks — *did any task actually
pass, and why were the other two ungraded?* The pipeline **computes** `resolved`
(`sum(t.success)`) and prints it live, then discards it; the persisted run record
carries only aggregate `metrics` plus a bare `graded_n` count. Every `None` degrade
point in the grader collapsed to the same indistinguishable `None`, so "2 of 3 tasks
couldn't be checked out" was indistinguishable from "the memory regressed accuracy
to zero." A floored accuracy on a flaky host env reads identically to a real failure.

The grader's public type is `Grader = Callable[[Task, str], Optional[bool]]`, used by
the harness, the native evaluators, and offline tests. `schema.py`/`protocols.py` are
frozen. So the fix must add *visibility* without changing either the callable
contract or the frozen schema.

In parallel, PR #124 added the grader-side half of this: `LocalExecGrader` now records
the *why* of each `None` on `self.last_reason` (cleared on a real verdict) and tallies
`self.ungraded_reasons`, logging each at WARNING ("loud degradation"). This ADR builds
**on** that — it is the **consumer** side: get that per-task reason out of the grader
instance and into the persisted run output so a saved result is self-explaining.

## Options considered

1. **Widen the `Grader` return type to `(success, reason)`** (or a `GradeOutcome`
   tuple + a `grade_with_reason()` sibling method). Explicit, but introduces a
   *second* reason mechanism alongside the grader's `last_reason` — two sources of
   truth to keep in sync — and adds method surface. Rejected once #124 landed
   `last_reason`: the reason already exists on the grader; don't duplicate it.
2. **Add a `success_reason` field to the `Trajectory` schema.** Clean data model,
   but `schema.py` is frozen — a `[CONTRACT]` change requiring all four owners, for
   what is an eval-internal diagnostic. Disproportionate.
3. **Consume the grader's existing `last_reason`: read it in the agent loop after
   each grade, bucket it, carry it on the existing `Trajectory.metadata` dict, and
   aggregate into the run `reliability` dict + summary.** No new grader surface, no
   frozen-schema change, single source of truth (the grader). Chosen.

## Decision

The grader-side reason is **already** produced by `LocalExecGrader.last_reason`
(PR #124). This ADR consumes it:

`agent._grade(task, pred, grader)` calls the grader for the verdict, then — on a
`None` (ungraded) — reads `grader.last_reason` and normalizes it to a stable bucket
(`_bucket_ungraded_reason`: `checkout_failed`, `patch_apply_failed`,
`gold_test_apply_failed`, `env_build_failed`, `no_selectors`, `exception`; an
unrecognized reason is kept verbatim). A real verdict clears `last_reason` → bucket
`"graded"`. The reason is recorded on `trajectory.metadata["grade_reason"]`.

Per run, the reliability dict gains `resolved` (passed count), `ungraded` (None
count), and `grade_reasons` (a histogram keyed by bucket). The pipeline summary
renders a `resolved` column on the metric table and a **Task grading** section
(`resolved / graded / ungraded / reasons`).

## Rationale

The degrade reason already lives on the grader instance after #124; the only thing
missing was getting it into the *persisted* run record (the live WARNING logs vanish;
`ungraded_reasons` is a run-lifetime tally, not per-task). Reading `last_reason`
right after the verdict and stashing it on the trajectory's existing `metadata` dict
(which already round-trips to JSONL) gives full per-task visibility with zero new
grader surface and zero frozen-contract churn. A run now reads as "1/3 resolved, 2
ungraded(checkout_failed)" instead of an uninterpretable "accuracy 0.0000."

## Tradeoffs & risks

- **`metadata` is a stringly-typed side channel, not a typed field.** A reader must
  know the `grade_reason` key exists. Mitigated by the histogram helper bucketing
  anything unknown as `"unknown"`. If consumers proliferate, promote it to a typed
  field via a `[CONTRACT]` schema change — this ADR is the migration's rationale.
- **Reason buckets are derived from free-form `last_reason` strings.** A wording
  change in the grader's reason strings could fall through to the verbatim fallback.
  Acceptable — the fallback keeps the raw string (nothing lost) and the buckets are
  unit-tested; if the grader formalizes its reasons, the bucket map points at them.
- **Coarse reasons for non-`LocalExecGrader` graders.** `overlap`/`none`/plain
  callables expose no `last_reason`, so they yield `graded` / `ungraded` / `no_grader`.
  Acceptable — the detail that mattered was the local exec env.
- **Reason is best-effort, not load-bearing.** Nothing gates on it; it is
  diagnostic. A wrong/missing reason never changes a `success` verdict or a metric.

## Consequences for the build

- **Policy consequence** — the grader remains the single source of the ungraded
  reason via `last_reason` (per PR #124); any new degrade point there MUST set it.
  Any new task-runner that sets `traj.success` SHOULD also read the grader's
  `last_reason` and record `traj.metadata["grade_reason"]` (the agent loop and native
  evaluators already do, via `agent._grade`).
- **Mechanism (not a cross-cutting contract — `contract: false`):**
  - **Reason source:** `LocalExecGrader.last_reason` (`grader.py`, owned by #124).
  - **Bucketing + carry:** `agent._grade` / `_bucket_ungraded_reason` map it to a
    stable label and stash it on `Trajectory.metadata["grade_reason"]: str` (no
    `schema.py` change — `metadata` is an existing free-form dict).
  - **Aggregation:** the `reliability` block in `agent.py` adds `resolved: int`,
    `ungraded: int`, `grade_reasons: dict[str, int]`; `results.py` passes them
    through; `pipeline_summary.py` renders the `resolved` column + Task grading
    section. These are eval-internal output fields, not a shape other workstreams
    build against — hence not a contract ADR.
