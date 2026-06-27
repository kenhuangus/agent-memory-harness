# Feature: SWE-Bench Docker Grader

**Status:** Done — with deferrals · **Date:** 2026-06-27

## What this delivers (before -> after)
Before: SWE-Bench-CL Django tasks that require historical Python/container setup often degrade to UNGRADED under the host grader. After: operators can opt into SWE-bench's Docker harness for CODE grading from the same `make pipeline` command.

## Requirements & acceptance criteria
1. Given a SWE-Bench-CL pipeline run, when the operator passes `--grader swebench-docker`, then CODE tasks are graded through SWE-bench's Docker harness instead of the host venv grader.
2. Given the existing default pipeline command, when no Docker grader is requested, then the current `swebench` host grader behavior is unchanged.
3. Given Docker, the `swebench` package, or the daemon is unavailable, when `--grader swebench-docker` is used, then the grader returns `None` with a visible ungraded reason rather than crashing the run.
4. Given tests run offline, when the Docker grader flow is exercised, then tests can fake the Docker/SWE-bench integration without requiring a daemon, network, or real images.

## Approach
Add a new opt-in `SwebenchDockerGrader` beside the existing host grader. It adapts a `Task` into the official SWE-bench instance/prediction shapes, calls SWE-bench's `make_test_spec`, optional environment image build, and `run_instance`, then maps the result back to the harness `True` / `False` / `None` contract. The pipeline and bench CLIs route `--grader swebench-docker` through the existing grader resolver.

## Build plan
- [x] Add ADR-eval-008 and index it; mark ADR-eval-002 superseded only for the Docker grading exception.
- [x] Implement `SwebenchDockerGrader` and register aliases.
- [x] Wire CLI help and resolver arguments for `--grader swebench-docker`.
- [x] Add offline unit tests with injected fake Docker/SWE-bench functions.
- [x] Run impacted pytest files.

## Quality bars
Security/trust boundary: unchanged; the harness still applies the agent patch and the SWE-bench test patch inside the grader path, and the model never self-grades.
Non-functional: Docker is opt-in because image pulls/builds are heavy and network/daemon dependent.
Observability: `last_reason` and `ungraded_reasons` mirror the host grader so summaries keep explaining missing grades.

## Decisions, assumptions & blockers
Decisions made:
- Docker grading is opt-in under `swebench-docker`; the default `swebench` host grader stays unchanged.
- The default Docker namespace is `swebench`, matching the upstream CLI's prebuilt-image path; passing namespace `none` enables local image builds.

Assumptions:
- The SWE-Bench-CL instance ids and fields match SWE-bench's Docker image/test-spec expectations.
- Operators who choose Docker have a working daemon and enough disk/network for image pulls or builds.

Deferred / blockers:
- No live Docker run is part of this change; validation is offline/unit-level unless a daemon is available.
- Full `eval` suite still has an unrelated plugin worker race test failure: its spy does not accept the current `model=` keyword passed by `sandbox.setup_real_plugin`.
