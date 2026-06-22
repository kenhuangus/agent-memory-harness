---
id: ADR-eval-002
domain: eval
title: Docker removed entirely — Claude Code CLI is the coding agent; LocalExecGrader / retrieval replace the SWE-bench Docker grader
status: Accepted
date: 2026-06-22
contract: false
supersedes: none
superseded_by: none
owner: Ken (P2)
origin: user directive (overrides the design doc's "keep Docker opt-in")
---

# ADR-eval-002: Docker removed entirely — Claude Code CLI is the coding agent; LocalExecGrader / retrieval replace the SWE-bench Docker grader

**Status:** Accepted · **Date:** 2026-06-22 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
CODE tasks (SWE-ContextBench, SWE-Bench-CL) were originally scored by an
`SWEBenchDockerGrader` that ran the official SWE-bench harness in a per-task
Docker container, gated behind an optional `swebench` extra. Two problems
compounded:

1. **The solve path bypassed the repo and memory.** `ClaudeCodeAgent`'s CODE
   branch ran a single "emit a unified diff" turn over the problem text — no
   checkout, no file reads, no test runs, and (critically) **no memory**: CODE
   never recorded a single `retrieve` step, so the whole point of a *memory*
   harness was untested on the coding benchmarks. In practice the model emitted
   prose, not a diff, and every CODE task graded `False`/ungraded.
2. **Docker is a heavy, host-fragile, Linux-only dependency.** `swebench` imports
   `resource` (Linux-only); grading required a running Docker daemon, multi-GB
   per-instance image pulls, and on Windows the Docker Desktop WSL-integration
   socket dropping mid-run — exactly the failure the old grader's
   `on_unavailable='skip'` plumbing existed to paper over.

The user directive is explicit and stronger than the design doc's original
"keep Docker opt-in" stance: **the Claude Code CLI must be the genuine coding
agent, and Docker must be removed ENTIRELY** — no `SWEBenchDockerGrader`, no
`swebench` extra, and no Docker references anywhere in code, configuration, or
user-facing documentation. This ADR records that complete removal — both the
architectural change to the solve/grade path and the documentation/config scrub
that makes the container-free, agent-driven design the only thing the project
describes.

## Options considered
- **Keep the SWE-bench Docker grader (status quo).** Faithful, leaderboard-comparable
  scores. Rejected per directive — and it is the source of the host-fragility,
  the Linux-only constraint, and the offline-untestability above.
- **Container-free remote grader (`sb-cli` / hosted infra).** Removes the local
  Docker daemon but keeps an external dependency + network + the `swebench`
  contract, and still doesn't make the solve path agentic. Rejected: doesn't meet
  "Docker removed entirely" in spirit and adds a hosted dependency.
- **Agentic loop + host-local test execution (chosen).** Drive `claude` as a real
  coding agent in a checkout and grade by running the project's tests on the host.
  No daemon, no extra package, fully offline-testable via injected runners. The
  cost — host-dependence and partial coverage — is acceptable *given* the honesty
  rule below.

## Decision
1. **The Claude Code CLI is the coding agent (agentic CODE solve).** A new
   `--code-mode agentic` (the default) drives `claude` as a real software engineer
   in a fresh working checkout of the task's repo at `base_commit`: full native
   toolset (Read/Edit/Bash), `permission_mode=acceptEdits`, the model **edits
   files directly and runs tests** (it must not print a diff). The harness captures
   `git diff` as the prediction. This is a genuine checkout/edit/run loop, not a
   one-turn "emit a diff" prompt. `--code-mode blind` preserves the prior one-turn
   "emit a diff" behavior for comparison.
2. **Memory in the CODE loop.** The agentic path wires the existing `off` /
   `builtin` / `plugin` / `plugin-real` memory exactly as the QA path does
   (reusing the team's seeding + recall-attribution untouched), so CODE finally
   records `retrieve` steps and the memory metrics apply to coding tasks.
3. **`LocalExecGrader` and retrieval metrics replace `SWEBenchDockerGrader`.**
   - `swe_contextbench` / `swe_bench_cl` → `LocalExecGrader`: provision a fresh
     checkout, apply the agent's prediction, then apply the **gold `test_patch`**
     — *the harness applies the tests, never the agent* (the trust boundary) —
     build a per-task venv best-effort, run `FAIL_TO_PASS` + `PASS_TO_PASS` on the
     host, and decide RESOLVED by the SWE-bench rule via the reused
     `resolved_from_report`.
   - `contextbench` → **retrieval-only**: scored by its native
     recall/precision/F1 over gold spans, no test execution (grader `None`).
   - QA benchmarks grade by normalized exact match. No grader involves a container
     runtime or an external grading package.
4. **`success=None` trust boundary.** The agentic solve returns
   `AgentResult(success=None)` so the **harness grader, never the model, owns the
   verdict**. The grader returns `None` (UNGRADED, excluded from accuracy)
   whenever the env can't be built or the checkout/patch can't be set up — never a
   fake `False`, never a crash.
5. **Docker removed entirely — not opt-in, not a fallback, not a footnote.**
   `SWEBenchDockerGrader`, the `_is_docker_unavailable` / `_Unavailable`
   machinery, the `swebench` lazy import, the `DEFAULT_DATASET` constant, the
   `swebench` pyproject extra, and all `--grader docker/swebench` +
   `--grader-on-unavailable` plumbing are deleted. Every remaining Docker /
   `swebench`-package reference outside this ADR is scrubbed from
   `eval/pyproject.toml`, `README.md`, `eval/README.md`,
   `eval/memeval/claudecode/README.md`, `results/v0.1/README.md`,
   `benchmarks.html`, `prd.md`, `eval/PROTOCOL.md`, and the residual code/test
   comments (`eval/memeval/grader.py`, `eval/tests/test_smoke.py`,
   `eval/tests/test_claudecode_code_agent.py`, `eval/tools/_measure.sh`).
6. **Single source of truth.** All docs describe one container-free pipeline; this
   ADR is the canonical reference the docs link to.

## Rationale
The directive demands a genuine coding agent and zero Docker; the agentic loop +
host venv is the only option that delivers both *and* is fully provable offline
(stub repo + injected git/command/CLI runners). The non-negotiable that keeps the
numbers trustworthy is the `success=None` trust boundary: the model can edit code
but cannot grade itself, and the harness — not the agent — applies the gold tests.
Removing the dependency in code while leaving it in the docs is not removing it: a
reader who finds a `swebench` extra in `pyproject.toml` or "no Docker daemon
needed" phrasing in a README reasonably infers Docker is still a supported mode,
so the scrub of the public surface area is part of the same decision.

## Tradeoffs & risks (reproducibility trade-off)
- **Not official SWE-bench numbers / not leaderboard-comparable.** Host-local
  execution is host-dependent and only partial-coverage; numbers it produces MUST
  NOT be compared to a containerized SWE-bench leaderboard. This reproducibility
  loss is the deliberate, accepted cost of removing Docker entirely, and is stated
  in every user-doc touching CODE grading.
- **Multilingual SWE-ContextBench is largely un-gradeable on a single host** (51
  repos, 9 languages, per-repo toolchains). Those instances return `None`
  (ungraded) and drop out of the accuracy denominator rather than scoring a false
  `False`. The `success=None` honesty rule is what keeps the reported accuracy
  trustworthy despite partial coverage.
- **Real runs still need network + subscription auth + a buildable repo.** Offline
  tests prove the loop with a stub repo + injected git/command/CLI runners only; a
  real swe_contextbench run needs a GitHub fetch-by-SHA, live `claude`
  subscription auth, and a repo whose env builds. Mitigation: the `None`-on-failure
  honesty rule means an unbuildable env never corrupts the reported accuracy.
- **Reproducibility drifts with the host.** Mitigated by pinning `base_commit`,
  applying gold tests from the dataset, and recording the resolved rule explicitly.

## Consequences for the build
- **Policy:** the agentic CODE path MUST return `success=None` — the grader owns
  the verdict; an agent that self-grades CODE is a bug.
- **Policy:** any inability to build the env or run the tests grades to `None`
  (UNGRADED), never `False` and never an exception that aborts the run.
- **Policy:** the harness applies the gold `test_patch`; the agent never touches
  the tests (the trust boundary).
- **Policy:** no Docker / `swebench`-package references in code, config, or
  user-docs. The only legitimate remaining mentions of "docker"/"swebench" are
  (a) this ADR, which records the removal decision, and (b) the benchmark's own
  canonical name — `SWE-Bench-CL` / `SWEBenchCLLoader` and the frozen `schema.py`
  alias `"swebench_cl"` — which name a dataset we load, not the Docker grading
  package. Real CODE scoring needs no extra install; `--grader auto` picks `local`
  for the SWE benches and `None` for QA + contextbench.
- **Scope:** the team's memory mechanism is reused unchanged
  (`eval/memeval/stores/**`, `okf.py`, `router.py`, `protocols.py`,
  `claudecode/service.py`, `claudecode/memory_server.py`, the harness scorer); the
  only memory-side edit is a pure extract-to-helper refactor of the plugin
  seed+attribute steps so the CODE path can call them, guarded by existing tests.
- **Frozen contract untouched:** `schema.py` already carries every CODE field and
  `AgentResult(prediction, patch, success)`; no schema change.
