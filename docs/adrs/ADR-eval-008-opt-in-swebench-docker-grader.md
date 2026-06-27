---
id: ADR-eval-008
domain: eval
title: Reintroduce Docker only as an opt-in SWE-bench grader for historical environment coverage
status: Accepted
date: 2026-06-27
contract: false
supersedes: ADR-eval-002
superseded_by: none
owner: Ken (P2)
origin: django_django_sequence host-grader Python-version failures
---

# ADR-eval-008: Reintroduce Docker only as an opt-in SWE-bench grader for historical environment coverage

**Status:** Accepted · **Date:** 2026-06-27 · **Contract:** no
**Supersedes:** [ADR-eval-002](ADR-eval-002-docker-free-code-grading.md) for the Docker-grading prohibition only · **Superseded by:** none

## Context

[ADR-eval-002](ADR-eval-002-docker-free-code-grading.md) removed Docker entirely and replaced the SWE-bench container grader with host-local grading. [ADR-eval-006](ADR-eval-006-grader-historical-env-compat.md) then patched several host-era compatibility problems, but the `django/django` SWE-Bench-CL sequence still exposes the limitation: many historical tasks require old Python and environment details that are unreliable or unavailable through a modern host `uv` venv.

The pipeline needs a way to recover grade coverage for these cases without changing the memory-agent solve path or making Docker a default dependency again.

## Options considered

- **Keep host grading only.** Simple and consistent with ADR-eval-002, but leaves many Django tasks ungraded for infrastructure reasons rather than task reasons.
- **Switch the default grader back to Docker.** Highest SWE-bench environment fidelity, but reintroduces the daemon/network/disk dependency for every CODE run and reverses more of ADR-eval-002 than needed.
- **Add an explicit Docker grader option.** Keeps the current default fast/offline-ish host path while allowing operators to pay the Docker cost when they need historical environment fidelity.

## Decision

Add `--grader swebench-docker` as an opt-in CODE grader. It adapts the existing `Task` and prediction into SWE-bench's official instance and prediction shapes, then delegates container setup, patch application, test execution, and report generation to the installed `swebench` package's Docker harness.

The existing `--grader swebench` host grader remains the pipeline default. Docker is never used unless selected by grader name.

## Rationale

The problem is not the agent, memory substrate, parser, or scoring fold; it is the environment. The official SWE-bench Docker harness is the environment authority, so the narrowest credible fix is to call it only when an operator needs that fidelity.

## Tradeoffs & risks

- **Operational cost returns for opt-in runs.** Docker grading may pull or build large images and requires a working daemon. This is acceptable because it is explicit per run.
- **Less offline-friendly.** Unit tests must fake the Docker/SWE-bench calls; live coverage depends on local Docker state and network/cache availability.
- **Result comparability changes by grader.** Host-grader and Docker-grader outputs should not be mixed without recording the grader field already stored in pipeline metadata.

## Consequences for the build

- `--grader swebench-docker` is the only supported Docker entry point.
- `--grader swebench` continues to mean the Docker-free host grader that reuses SWE-bench specs and parsers.
- Docker infrastructure failures degrade to `None` with a visible reason; they do not crash a pipeline run or score as task failures.
- Documentation may mention Docker only for this explicit opt-in grader path or for historical ADR context.
