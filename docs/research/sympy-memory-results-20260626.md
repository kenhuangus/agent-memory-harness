# SymPy SWE-Bench-CL memory result comparison, 2026-06-26

> Status: findings from existing result artifacts. No benchmark code is changed by
> this document.
> Scope: SymPy `swe_bench_cl` sequence runs under `results/vsympy_sympy_sequence-*`.

## TL;DR

The two most recent SymPy memory runs are an apples-to-apples pair on
`git_sha=45a508b`:

- `builtin`: `results/vsympy_sympy_sequence-builtin-45a508b-1`
- `plugin-accum`: `results/vsympy_sympy_sequence-plugin-accum-45a508b-1`

`plugin-accum` solved 33/50 tasks, while `builtin` solved 32/50. That is a small
+1 resolved task / +2 point accuracy gain for accumulated plugin memory, with
high observed memory reach and hit counts.

Compared with the older `54d168e` SymPy results, both newest runs improved over
the prior no-plugin baselines, but the strongest fair comparison is the
same-commit `45a508b` pair. The older `plugin-blank` run was worse than its
same-commit base run, which suggests that merely installing the plugin is not
enough; the useful signal appears only after carrying forward accumulated memory.

## Source artifacts

Primary pair:

- `results/vsympy_sympy_sequence-builtin-45a508b-1/SUMMARY-swe_bench_cl-20260626T045823Z.json`
- `results/vsympy_sympy_sequence-plugin-accum-45a508b-1/SUMMARY-swe_bench_cl-20260626T045944Z.json`

Older SymPy comparison points:

- `results/vsympy_sympy_sequence-base-54d168e-1/SUMMARY-swe_bench_cl-20260625T044323Z.json`
- `results/vsympy_sympy_sequence-plugin-blank-54d168e-1/SUMMARY-swe_bench_cl-20260625T044548Z.json`

## Aggregate comparison

| Run | Commit | Stage | Timestamp | Resolved | Graded | Accuracy | Cost | Memory reached | Memory hits | Recall attempted | Recall with hits | Durable items after |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `vsympy_sympy_sequence-base-54d168e-1` | `54d168e` | `base` | `20260625T044323Z` | 30 | 49 | 61.22% | $4.9960 | 0 | 0 | 0 | 0 | 0 |
| `vsympy_sympy_sequence-plugin-blank-54d168e-1` | `54d168e` | `plugin-blank` | `20260625T044548Z` | 29 | 50 | 58.00% | $2.4728 | 43 | 42 | 43 | 42 | 30 |
| `vsympy_sympy_sequence-builtin-45a508b-1` | `45a508b` | `builtin` | `20260626T045823Z` | 32 | 50 | 64.00% | $4.1903 | 0 | 0 | 0 | 0 | 0 |
| `vsympy_sympy_sequence-plugin-accum-45a508b-1` | `45a508b` | `plugin-accum` | `20260626T045944Z` | 33 | 50 | 66.00% | $4.5791 | 48 | 48 | 48 | 48 | 95 |

## Findings

1. `plugin-accum` is the best SymPy run in this result set.
   It reaches 66% accuracy and 33 resolved tasks. The next best run is the
   same-commit `builtin` result at 64% and 32 resolved tasks.

2. The accumulated-memory comparison is positive but modest.
   Against `builtin` on the same commit, `plugin-accum` improves by 1 resolved
   task out of 50. Cost increases from $4.1903 to $4.5791, about +9.3%.

3. Plugin installation alone previously looked harmful on SymPy.
   The older `plugin-blank` run solved 29/50, below the same-commit `base` run's
   30/49 graded result. That older pair is not directly comparable to the
   newest pair because the harness commit changed from `54d168e` to `45a508b`,
   but it is still useful as a warning: an empty plugin surface can add overhead
   without improving outcomes.

4. Accumulated plugin memory was actually exercised in the newest run.
   `plugin-accum` reports memory reached/hit/attempted/with-hit counts of
   48/48/48/48. The source memory copied from `plugin-blank` had 30 durable
   items; after `plugin-accum`, durable items reached 95.

5. The source memory health is consistent with a real accumulated-memory run.
   The `plugin-accum` summary records source memory copied from
   `results/vsympy_sympy_sequence-plugin-blank-54d168e-1/_memory`, with 706
   events, 2,343 daydream events, 10 recall events, 9 recall events with hits,
   and 30 durable items before the new run.

6. Reliability noise still matters.
   The newest `builtin` run had 3 solve-stage errors:
   `sympy__sympy-20916`, `sympy__sympy-14976`, and `sympy__sympy-18698`.
   The newest `plugin-accum` run had 2 solve-stage errors:
   `sympy__sympy-20916` and `sympy__sympy-13615`.
   Because full per-task resolved/fail records are not present in the exported
   JSON, the +1 aggregate result cannot be attributed to a specific task from
   these artifacts alone.

## Interpretation

The cleanest conclusion is that accumulated plugin memory is now directionally
helpful on SymPy, but the observed effect size is small: +1 resolved task over
the no-plugin builtin run on the same commit.

This is still meaningful because the older `plugin-blank` result showed that the
plugin surface can hurt when it has no useful accumulated memory. The newest
`plugin-accum` run reverses that pattern: it uses prior durable memories heavily
and becomes the top SymPy result in the current set.

The result should not yet be treated as definitive proof that memory improves
SymPy SWE-Bench-CL performance. The benchmark exports do not include task-level
pass/fail records, and the runs still contain solve-stage timeout/API failures.
A follow-up should preserve per-instance outcomes so we can identify exact
win/loss flips and separate memory effects from run reliability variance.

## Recommended next steps

1. Preserve per-task grade outcomes in `swe_bench_cl-*.json` exports, not only
   aggregate counts and error lists.
2. Re-run the same `45a508b` builtin/plugin-accum pair with the same budget to
   estimate variance.
3. Add a report that diffs solved task IDs across stages when per-task records
   are available.
4. Track memory retrieval payload quality for the 48 `plugin-accum` tasks with
   hits, so aggregate wins can be connected to concrete retrieved memories.

## Update: dreamed plugin run at `8c48b84`

The `plugin-dreamed` run in
`results/vsympy_sympy_sequence-plugin-dreamed-8c48b84-1` copied the accumulated
SymPy memory from `results/vsympy_sympy_sequence-plugin-accum-45a508b-1/_memory`,
ran the dream consolidation pipeline, and then evaluated the resulting memory
store.

### Source artifacts

- `results/vsympy_sympy_sequence-plugin-dreamed-8c48b84-1/SUMMARY-swe_bench_cl-20260626T170808Z.json`
- `results/vsympy_sympy_sequence-plugin-dreamed-8c48b84-1/swe_bench_cl-20260626T170808Z.json`
- Comparison source:
  `results/vsympy_sympy_sequence-plugin-accum-45a508b-1/SUMMARY-swe_bench_cl-20260626T045944Z.json`

### Aggregate comparison

| Run | Commit | Stage | Timestamp | Resolved | Graded | Ungraded | Accuracy | Efficiency | Cost | Memory reached | Memory hits | Recall attempted | Recall with hits | Durable items after |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `vsympy_sympy_sequence-builtin-45a508b-1` | `45a508b` | `builtin` | `20260626T045823Z` | 32 | 50 | 0 | 64.00% | 0.0000 | $4.1903 | 0 | 0 | 0 | 0 | 0 |
| `vsympy_sympy_sequence-plugin-accum-45a508b-1` | `45a508b` | `plugin-accum` | `20260626T045944Z` | 33 | 50 | 0 | 66.00% | 0.1691 | $4.5791 | 48 | 48 | 48 | 48 | 95 |
| `vsympy_sympy_sequence-plugin-dreamed-8c48b84-1` | `8c48b84` | `plugin-dreamed` | `20260626T170808Z` | 30 | 49 | 1 | 61.22% | 0.2806 | $4.7297 | 49 | 49 | 49 | 49 | 158 |

### Dream consolidation result

The dream stage completed all configured jobs:

- `dedup_detection`
- `dedup_merge`
- `ttl_pruning`
- `contradiction_resolution`
- `governance`

It started from a source memory store with 95 durable items and produced a
post-run store with 158 durable items after the evaluation stage. The dream
summary reports 95 total items during consolidation, no duplicate clusters, no
retired or pruned items, no contradicted items, 22 `must_know` governance tags,
and 1 `must_do` tag.

The consolidation work therefore appears to have been operationally successful:
it ran every job, found no cleanup candidates, and classified a subset of memory
items as higher-priority knowledge. The evaluation stage also exercised memory
heavily, with recall attempted on 49 tasks and hits on all 49 attempts.

### Findings

1. `plugin-dreamed` did not improve the aggregate SymPy result.
   It solved 30/50 tasks, below the prior same-sequence `plugin-accum` result of
   33/50 and below the prior same-sequence `builtin` result of 32/50. The commit
   changed from `45a508b` to `8c48b84`, so this is not a strict A/B comparison,
   but the dreamed run is still directionally negative against the available
   SymPy reference points.

2. Memory was available and used, so the weaker result is not explained by a
   plugin wiring failure. The run reports `memory_reached=49`,
   `memory_hit=49`, `recall_attempted=49`, and `recall_with_hits=49`. Its
   preflight plugin memory probe also succeeded.

3. Dreaming increased memory volume and curation metadata, but the benchmark
   export does not show that those changes translated into better solves. The
   source accumulated-memory store had 95 durable items; after the dreamed run,
   durable items reached 158. The dream stage marked 22 items as `must_know` and
   1 as `must_do`, but no per-task outcome export is available to connect those
   tags to specific wins or losses.

4. The dreamed run had less solve-stage error noise than the prior accumulated
   run but still produced a lower resolved count. `plugin-dreamed` had one
   solve timeout (`sympy__sympy-20916`) and one ungraded task where the official
   parser produced no statuses. `plugin-accum` had two solve timeouts
   (`sympy__sympy-20916` and `sympy__sympy-13615`). That makes the aggregate
   drop harder to dismiss as pure reliability noise.

5. Cost rose while resolved count fell. The dreamed run cost $4.7297, compared
   with $4.5791 for `plugin-accum` and $4.1903 for `builtin`. The higher
   efficiency metric for `plugin-dreamed` reflects heavier memory/retrieval
   activity, not a better solve outcome.

### Interpretation

The current dreamed-memory result is evidence that dream consolidation is
running, durable, and visible to the agent, but not evidence that it improves
SymPy SWE-Bench-CL performance. On this run, the agent retrieved memory almost
everywhere and had a larger post-run memory store, yet solved fewer tasks than
the accumulated-memory run it was seeded from.

The most likely product conclusion is that the next bottleneck is memory quality
and ranking, not memory availability. Dream governance can identify potentially
important items, but the agent still needs the right memories at the right time,
with enough precision that retrieval does not add distracting context.

### Recommended next steps

1. Re-run `plugin-accum` and `plugin-dreamed` on the same harness commit before
   treating the 33-to-30 drop as a definitive dreaming regression.
2. Export per-task resolved statuses and retrieved memory IDs so future analysis
   can identify exact win/loss flips caused by dreamed memories.
3. Add retrieval-quality instrumentation for `must_know` and `must_do` items:
   whether they were retrieved, where they ranked, and whether the solve attempt
   used them.
4. Test a dreamed-memory variant that only promotes governance-approved
   `must_know` items into high-rank retrieval, rather than simply carrying
   forward a larger memory set.
