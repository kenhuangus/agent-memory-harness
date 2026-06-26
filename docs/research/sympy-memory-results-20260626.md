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
