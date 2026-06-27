# SWE-Bench-CL memory run findings, 2026-06-27

> Status: findings from result artifacts already produced by the harness. No
> benchmark code changes are made by this document.
>
> Scope: the 14 new run directories present in the working tree, completed from
> late June 26, 2026 through late morning June 27, 2026
> (`20260626T221413Z` through `20260627T152317Z`). Where xarray needed a fair
> baseline, this also references adjacent late-June-26 artifacts already present
> in `results/`.

## TL;DR

The clearest positive result is the Cursor SymPy plugin-accum run:
`vsympy_sympy_sequence-plugin-accum-1763e51-2` solved 48/50, compared with
45/50 for both Cursor base and Cursor builtin on the same commit. The exact
same-harness task diff against Cursor builtin is 3 wins and 0 losses:
`sympy__sympy-13798`, `sympy__sympy-15809`, and `sympy__sympy-20916`.

Django also moved in the right direction, but with a small effect size: Cursor
base solved 10/50, builtin solved 11/50, plugin-accum solved 12/50, and the first
dreamed run solved 13/50. A second dreamed run fell back to 12/50, so dreaming is
not yet stable.

The negative signal is that memory availability is no longer the bottleneck.
Plugin runs generally report recall attempts and hits on nearly every task, but
the outcome depends heavily on harness/model and memory quality. Claude SymPy
plugin/dreamed runs were much weaker than Cursor runs, and later dreamed SymPy
runs regressed from the 48/50 plugin-accum high-water mark.

## Source artifacts

Primary new run directories:

- `results/vdjango_django_sequence-base-1763e51-1`
- `results/vdjango_django_sequence-builtin-1763e51-1`
- `results/vdjango_django_sequence-plugin-accum-1763e51-1`
- `results/vdjango_django_sequence-plugin-blank-a1677d1-1`
- `results/vdjango_django_sequence-plugin-dreamed-1763e51-1`
- `results/vdjango_django_sequence-plugin-dreamed-1763e51-2`
- `results/vsympy_sympy_sequence-base-1763e51-1`
- `results/vsympy_sympy_sequence-builtin-1763e51-1`
- `results/vsympy_sympy_sequence-plugin-accum-1763e51-1`
- `results/vsympy_sympy_sequence-plugin-accum-1763e51-2`
- `results/vsympy_sympy_sequence-plugin-dreamed-1763e51-1`
- `results/vsympy_sympy_sequence-plugin-dreamed-1763e51-2`
- `results/vsympy_sympy_sequence-plugin-dreamed-1763e51-3`
- `results/vsympy_sympy_sequence-plugin-dreamed-1763e51-4`

Adjacent xarray references used for comparison:

- `results/vpydata_xarray_sequence-base-a1677d1-1`
- `results/vpydata_xarray_sequence-builtin-a1677d1-1`
- `results/vpydata_xarray_sequence-plugin-blank-a1677d1-1`
- `results/vpydata_xarray_sequence-plugin-accum-a1677d1-1`

## New Run Matrix

| Run | Harness/model | Stage | Timestamp UTC | Resolved | Graded | Accuracy | Cost | Recall hits/reached | Errors | Notes |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `vdjango_django_sequence-base-1763e51-1` | Cursor composer-2.5 | base | `20260627T043540Z` | 10 | 50 | 20.00% | $0.0000 | 0/0 | 0 | No memory |
| `vdjango_django_sequence-builtin-1763e51-1` | Cursor composer-2.5 | builtin | `20260627T043603Z` | 11 | 50 | 22.00% | $0.0000 | 0/0 | 1 | Builtin memory path, no recall counters |
| `vdjango_django_sequence-plugin-accum-1763e51-1` | Cursor composer-2.5 | plugin-accum | `20260627T043654Z` | 12 | 50 | 24.00% | $0.0000 | 50/50 | 0 | Source memory had 31 durable items |
| `vdjango_django_sequence-plugin-blank-a1677d1-1` | Cursor composer-2.5 | plugin-blank | `20260626T221413Z` | 12 | 49 | 24.49% | $0.0000 | 48/50 | 0 | One ungraded task |
| `vdjango_django_sequence-plugin-dreamed-1763e51-1` | Cursor composer-2.5 | plugin-dreamed | `20260627T074918Z` | 13 | 50 | 26.00% | $0.0000 | 50/50 | 0 | Dreamed over 59 items, 20 must-know |
| `vdjango_django_sequence-plugin-dreamed-1763e51-2` | Cursor composer-2.5 | plugin-dreamed | `20260627T151636Z` | 12 | 50 | 24.00% | $0.0000 | 50/50 | 0 | Dreamed over 84 items, 39 must-know |
| `vsympy_sympy_sequence-base-1763e51-1` | Cursor composer-2.5 | base | `20260627T043720Z` | 45 | 50 | 90.00% | $0.0000 | 0/0 | 0 | No memory |
| `vsympy_sympy_sequence-builtin-1763e51-1` | Cursor composer-2.5 | builtin | `20260627T043741Z` | 45 | 50 | 90.00% | $0.0000 | 0/0 | 0 | Same aggregate as base |
| `vsympy_sympy_sequence-plugin-accum-1763e51-1` | Claude Haiku 4.5 | plugin-accum | `20260627T043512Z` | 35 | 50 | 70.00% | $4.6154 | 50/50 | 0 | Not comparable to Cursor base/builtin |
| `vsympy_sympy_sequence-plugin-accum-1763e51-2` | Cursor composer-2.5 | plugin-accum | `20260627T043902Z` | 48 | 50 | 96.00% | $0.0000 | 50/50 | 0 | Best run in this batch |
| `vsympy_sympy_sequence-plugin-dreamed-1763e51-1` | Cursor composer-2.5 | plugin-dreamed | `20260627T081429Z` | 45 | 50 | 90.00% | $0.0000 | 50/50 | 0 | Dreamed over 48 items, 34 must-know |
| `vsympy_sympy_sequence-plugin-dreamed-1763e51-2` | Claude Haiku 4.5 | plugin-dreamed | `20260627T081517Z` | 34 | 50 | 68.00% | $4.1279 | 48/48 | 2 | One contradiction flagged |
| `vsympy_sympy_sequence-plugin-dreamed-1763e51-3` | Cursor composer-2.5 | plugin-dreamed | `20260627T151805Z` | 41 | 50 | 82.00% | $0.0000 | 49/49 | 1 | 15 must-know, 4 must-do |
| `vsympy_sympy_sequence-plugin-dreamed-1763e51-4` | Claude Haiku 4.5 | plugin-dreamed | `20260627T152317Z` | 26 | 42 | 61.90% | $3.7470 | 41/41 | 2 | Partial: 43/50 tasks attempted, 1 ungraded |

## Findings

1. The strongest positive signal is Cursor SymPy plugin-accum.
   `vsympy_sympy_sequence-plugin-accum-1763e51-2` solved 48/50, improving over
   same-commit Cursor base and builtin at 45/50. Memory was fully exercised:
   50 recall attempts, 50 tasks reached memory, and 50 hits.

2. Django shows a small monotonic improvement through the first dreamed run.
   Same-commit Cursor runs moved from 10/50 base to 11/50 builtin to 12/50
   plugin-accum to 13/50 first plugin-dreamed. The exact step wins were
   `django__django-11490` for builtin over base, `django__django-14376` for
   plugin-accum over builtin, and `django__django-14315` for first dreamed over
   plugin-accum.

3. Dreaming is operational but not yet predictably beneficial.
   Django dreamed-1 improved to 13/50, but dreamed-2 dropped back to 12/50 and
   specifically lost `django__django-14315`. SymPy dreamed-1 returned to the
   45/50 baseline after the 48/50 plugin-accum run. SymPy dreamed-3 reached
   41/50, while dreamed-4 was partial at 26/42. The dream jobs ran and produced
   governance labels, but the aggregate solve results are unstable.

4. Memory wiring is healthy.
   Plugin runs usually report recall hits on every reached task:
   Django plugin/dreamed runs hit 48-50 tasks, SymPy Cursor plugin/dreamed runs
   hit 49-50 tasks, and the Claude plugin runs also show high hit rates. Poor
   outcomes are therefore not explained by the agent failing to reach memory.

5. Harness/model differences dominate several apparent memory deltas.
   SymPy Cursor rows should not be directly mixed with SymPy Claude rows. On the
   same `1763e51` commit, Cursor base/builtin/plugin-accum produced
   45/50, 45/50, and 48/50, while Claude plugin-accum/dreamed produced
   35/50, 34/50, and a partial 26/42. Those lower Claude numbers are useful
   reliability data, but they are not a clean memory treatment comparison
   against Cursor baselines.

6. The xarray accumulated-memory result is neutral to mildly positive.
   On `a1677d1`, xarray base solved 6/22, builtin solved 5/22, plugin-blank
   solved 5/22, and plugin-accum solved 6/22. The plugin-accum row had only
   9 recall attempts/hits, because the source memory store was small. This is
   too small to claim improvement, but it does not show the clear regression
   seen in some older empty-plugin experiments.

7. Reliability noise still needs to be separated from memory effects.
   The new batch includes a Cursor Django builtin timeout, a Cursor SymPy
   dreamed-3 timeout, two Claude SymPy dreamed-2 timeouts, and a partial Claude
   SymPy dreamed-4 run with one API connection failure. The new per-task records
   make this diagnosable, but aggregate accuracy alone can still mislead.

## Dream Consolidation Notes

Dream consolidation completed its configured jobs in the dreamed runs:
dedup detection, dedup merge, TTL pruning, contradiction resolution, and
governance.

| Run | Total items | Duplicate clusters | Retired/pruned | Contradicted | Must-know | Must-do |
|---|---:|---:|---:|---:|---:|---:|
| `vdjango_django_sequence-plugin-dreamed-1763e51-1` | 59 | 0 | 0 | 0 | 20 | 0 |
| `vdjango_django_sequence-plugin-dreamed-1763e51-2` | 84 | 0 | 0 | 0 | 39 | 0 |
| `vsympy_sympy_sequence-plugin-dreamed-1763e51-1` | 48 | 0 | 0 | 0 | 34 | 0 |
| `vsympy_sympy_sequence-plugin-dreamed-1763e51-2` | 50 | 0 | 0 | 1 | 23 | 0 |
| `vsympy_sympy_sequence-plugin-dreamed-1763e51-3` | 64 | 0 | 0 | 0 | 15 | 4 |
| `vsympy_sympy_sequence-plugin-dreamed-1763e51-4` | 64 | 0 | 0 | 0 | 49 | 0 |

The practical read is that dreaming is mostly acting as governance/ranking
metadata today, not as cleanup. It found no duplicate clusters and pruned no
items in this batch. The one contradiction in SymPy dreamed-2 was effectively a
near-duplicate judgment: both memories advised using the sum of exponents for
monomial total degree filtering.

## Exact Task Flips

Django:

- Base to builtin: +`django__django-11490`, no losses.
- Builtin to plugin-accum: +`django__django-14376`, no losses.
- Plugin-accum to dreamed-1: +`django__django-14315`, no losses.
- Dreamed-1 to dreamed-2: lost `django__django-14315`.

SymPy:

- Base to builtin: won `sympy__sympy-19495`, `sympy__sympy-20428`; lost
  `sympy__sympy-13798`, `sympy__sympy-15809`. Net 0.
- Cursor builtin to Cursor plugin-accum: won `sympy__sympy-13798`,
  `sympy__sympy-15809`, and `sympy__sympy-20916`; no losses.
- Cursor base to Cursor plugin-accum: won `sympy__sympy-19495`,
  `sympy__sympy-20428`, and `sympy__sympy-20916`; no losses.
- Cursor dreamed-1 solved 45/50 and therefore did not retain the 48/50
  plugin-accum gain. Against Cursor plugin-accum it won `sympy__sympy-15017`
  but lost `sympy__sympy-13091`, `sympy__sympy-15599`,
  `sympy__sympy-19495`, and `sympy__sympy-20428`.
- Cursor dreamed-3 solved 41/50 and introduced a timeout on
  `sympy__sympy-21847`.

## Interpretation

The current evidence supports three conclusions.

First, accumulated plugin memory can help when the harness/model is held
constant. The best example is Cursor SymPy at `1763e51`, where plugin-accum
reached 48/50 against a 45/50 base/builtin baseline. Django shows the same
direction but only at +1 per stage.

Second, dream consolidation is not yet an outcome-improving treatment by itself.
It runs reliably and produces meaningful governance tags, but this batch does
not show stable benchmark gains after dreaming. The likely bottleneck is
retrieval quality and ranking, not whether memory is available.

Third, future analysis needs to segment by harness/model first, then memory
treatment. Cursor and Claude rows behave differently enough that mixing them
will overstate or understate memory effects.

## Recommended Next Steps

1. Re-run the Cursor SymPy `base`, `builtin`, `plugin-accum`, and
   `plugin-dreamed` stages on the same commit to confirm whether 48/50 is
   repeatable.
2. Add a report that groups comparisons by `(sequence, git_sha, harness, model)`
   before computing deltas.
3. For dreamed runs, log which `must_know` and `must_do` memories were retrieved
   per task and at what rank.
4. Treat partial runs separately in summary tables so reliability failures do
   not look like memory-quality failures.
