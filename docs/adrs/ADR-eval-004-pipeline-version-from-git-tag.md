---
id: ADR-eval-004
domain: eval
title: A pipeline run's version is the git tag on its `main` commit (fallback MEMORY_VERSION)
status: Proposed
date: 2026-06-22
contract: false
supersedes: none
superseded_by: none
owner: Ken (P2)
origin: design session 2026-06-22 (SWE-Bench-CL live-plugin pipeline)
---

# ADR-eval-004: A pipeline run's version is the git tag on its `main` commit (fallback `MEMORY_VERSION`)

**Status:** Proposed · **Date:** 2026-06-22 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context

The 5-stage pipeline writes a version-scoped results directory and, per
[`ADR-eval-003`](ADR-eval-003-pipeline-shared-memory-substrate.md), a version-scoped shared
memory substrate (`results/v{version}/_memory/`). The whole point of versioning the substrate
is that *a given generation of the memory code is benchmarked against its own accumulated
memory* — a new code generation must start from an empty substrate, or results silently mix
two implementations.

Today versioning is a hand-maintained constant: `memeval.MEMORY_VERSION = "0.1"`
([`eval/memeval/__init__.py`](../../eval/memeval/__init__.py)), bumped manually "by 0.1
whenever the memory code/storage changes and you re-run" (`architecture.md` §7.1). That works
for the results ledger but is too weak to key a *persistent memory substrate* on: a developer
who forgets to bump it accumulates memory from code generation N into the substrate of
generation N+1. The user's requirement is explicit: **a pipeline's version always matches the
version tag of the commit on `main`** — a tag is an immutable, intentional release marker, far
harder to forget than editing a constant.

## Options considered

- **Version = the git tag on the current `main` commit; fall back to `MEMORY_VERSION` when
  untagged** (chosen). Resolve via `git describe --tags --exact-match HEAD`; if HEAD is not
  exactly a tag, fall back to the nearest tag (`git describe --tags --abbrev=0`) or finally to
  `MEMORY_VERSION`. Normalize through the existing `results.normalize_version()` so `v0.2`,
  `0.2`, `V0.2` all become `v0.2`.
- **Keep the hand-edited `MEMORY_VERSION` constant only.** Rejected: too easy to forget, and it
  cannot distinguish "I cut a release" from "I edited a file"; keying a persistent substrate on
  it risks cross-generation contamination.
- **Hash the memory code (content-addressed version).** Rejected: a content hash changes on
  every trivial edit (whitespace, comments), fragmenting substrates and results into noise; and
  it is not the human-meaningful "which release" the user asked for.
- **Require a tag, hard-fail when untagged.** Rejected: too rigid for local iteration — a dev
  must be able to run the pipeline on an untagged WIP commit. The fallback keeps that working
  while logging loudly that the run is untagged.

## Decision

A pipeline run's **version** is resolved, in order:

1. `git describe --tags --exact-match HEAD` — HEAD is exactly a tag → use it.
2. else `git describe --tags --abbrev=0` — nearest reachable tag → use it, and record that
   HEAD is *past* that tag (not exact) in run metadata.
3. else the current **branch name** (`git rev-parse --abbrev-ref HEAD`, when not detached) →
   use a filesystem-safe form `vbranch-<sanitized-branch>` (slashes and other unsafe chars →
   `-`), so a WIP branch's substrate accumulates across local runs and stays isolated from a
   released version's. Flagged `untagged` (source `branch`).
4. else `memeval.MEMORY_VERSION` — detached HEAD / no branch / no git → final fallback,
   flagged `untagged` (source `memory-version`).

The result is normalized with `results.normalize_version()` to the `vX.Y…` directory form and
used as the `{version}` in both `results/v{version}/` (result files) and
`results/v{version}/_memory/` (the shared substrate, per ADR-eval-003). The resolver returns
`{version, version_exact, untagged, git_sha, branch, source}` where `source` is one of
`exact-tag | nearest-tag | branch | memory-version`.

**Why a branch rung (not straight to `MEMORY_VERSION`):** without it, every untagged branch
shared one `v{MEMORY_VERSION}` substrate, so two people iterating on different feature branches
would pollute each other's accumulating memory and a branch's runs would mix with `main`'s. Keying
by branch makes "this branch's memory" the natural local-iteration unit; a real benchmarked release
still runs on an exact tag (rung 1).

A small resolver `resolve_pipeline_version()` lives in
[`eval/memeval/results.py`](../../eval/memeval/results.py) (Ken's domain) alongside the
existing `normalize_version` / `run_timestamp` / `benchmark_results_path` helpers.

## Rationale

A git tag is the cheapest immutable, intentional, human-meaningful version marker we already
have, and the user named it as the contract. Keying the persistent substrate on the tag means
"new release ⇒ fresh memory" is automatic, while the `MEMORY_VERSION` fallback preserves
frictionless local iteration on untagged commits. Reusing `normalize_version` keeps one
directory-naming convention across the whole results tree.

## Tradeoffs & risks

- **Untagged commits on the SAME branch share that branch's bucket.** The branch rung means a
  branch's runs accumulate into `vbranch-<branch>/` regardless of which commit on the branch is
  checked out — so two different code states on one branch collide in that branch's substrate.
  This is the intended local-iteration behavior ("this branch's memory"), and it is far better
  than the prior all-untagged-share-`v{MEMORY_VERSION}` collision (different branches no longer
  pollute each other). The contract for *comparable, archival* runs is still "tag the commit"
  (rung 1); a dev can wipe `vbranch-<branch>/_memory/` to reset a branch's memory.
- **Detached HEAD with no tag** falls to `v{MEMORY_VERSION}` (no branch to key on) — the one
  remaining shared bucket, acceptable since detached-HEAD pipeline runs are unusual.
- **`git describe` needs a git checkout.** In a tarball/export with no `.git`, resolution falls
  to `MEMORY_VERSION`. Accepted: the pipeline is a developer/CI tool run from the repo.
- **Lightweight vs annotated tags.** `--tags` matches both, which is what we want; the resolver
  does not require annotated tags.
- **HEAD-past-a-tag ambiguity.** When HEAD is commits *after* a tag, option 2 uses that tag's
  name even though the code differs. Mitigation: metadata records `version_exact: false` and the
  short SHA, so a reader can tell the run was past the tag. For a real benchmarked release the
  expectation is to run on the exact tagged commit (option 1).

## Consequences for the build

- **Policy:** the pipeline resolves its version once at startup via `resolve_pipeline_version()`
  and threads it as the `{version}` for `results/v{version}/` AND `results/v{version}/_memory/`
  (ADR-eval-003). No other version source is consulted in the pipeline path.
- **Policy:** run metadata records `version`, `version_exact` (bool), `git_sha`, and an
  `untagged` flag when the fallback fired — so a results file is self-describing about which tag
  (or fallback) produced it.
- **Reuse:** `normalize_version`, `run_timestamp`, `benchmark_results_path`,
  `write_benchmark_results` in `results.py` are reused unchanged; only the resolver is added.
- **Relationship to `MEMORY_VERSION`:** the constant remains the documented fallback and the
  default for `memeval-bench`'s `--results-version` when not on a tagged commit; this ADR does
  not remove it, it demotes it to the fallback rung for the pipeline.
