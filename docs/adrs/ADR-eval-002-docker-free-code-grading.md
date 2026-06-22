---
id: ADR-eval-002
domain: eval
title: Docker-free CODE grading — agentic Claude Code loop + LocalExecGrader; SWE-bench Docker grader removed
status: Accepted
date: 2026-06-22
contract: false
supersedes: none
superseded_by: none
owner: Ken (P2)
origin: design doc docs/research/claude-code-coding-agent-benchmark.md + user directive
---

# ADR-eval-002: Docker-free CODE grading — agentic Claude Code loop + LocalExecGrader; SWE-bench Docker grader removed

**Status:** Accepted · **Date:** 2026-06-22 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
CODE tasks (SWE-ContextBench, SWE-Bench-CL) were scored by an
`SWEBenchDockerGrader` that ran the official SWE-bench harness in a per-task
Docker container, gated behind an optional `swebench` extra. Two problems compounded:

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

The user directive (overriding the design doc's "keep Docker opt-in") is explicit:
**Claude Code must be the genuine coding agent, and Docker must be removed
entirely** — no `SWEBenchDockerGrader`, no `swebench` extra, no Docker references
in code/config/user-docs.

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
1. **Agentic CODE solve.** A new `--code-mode agentic` (the default) drives
   `claude` as a real software engineer in a fresh working checkout of the task's
   repo at `base_commit`: full native toolset (Read/Edit/Bash),
   `permission_mode=acceptEdits`, the model **edits files directly and runs tests**
   (it must not print a diff). The harness captures `git diff` as the prediction.
   `--code-mode blind` preserves the prior one-turn "emit a diff" behavior.
2. **Memory in the CODE loop.** The agentic path wires the existing `off` /
   `builtin` / `plugin` / `plugin-real` memory exactly as the QA path does
   (reusing the team's seeding + recall-attribution untouched), so CODE finally
   records `retrieve` steps and the memory metrics apply to coding tasks.
3. **Host-local grading (`LocalExecGrader`).** Provision a fresh checkout, apply
   the agent's prediction, then apply the **gold `test_patch`** — *the harness
   applies the tests, never the agent* (the trust boundary) — build a per-task venv
   best-effort, run `FAIL_TO_PASS` + `PASS_TO_PASS`, and decide RESOLVED by the
   SWE-bench rule via the reused `resolved_from_report`.
4. **`success=None` ownership.** The agentic solve returns `AgentResult(success=None)`
   so the **harness grader, never the model, owns the verdict**. The grader returns
   `None` (UNGRADED, excluded from accuracy) whenever the env can't be built or the
   checkout/patch can't be set up — never a fake `False`, never a crash.
5. **ContextBench is retrieval-only** — scored by its native recall/precision/F1
   over gold spans, no test execution (grader `None`).
6. **Docker removed entirely.** `SWEBenchDockerGrader`, the `_is_docker_unavailable`
   /`_Unavailable` machinery, the `swebench` lazy import, the `DEFAULT_DATASET`
   constant, the `swebench` pyproject extra, and all `--grader docker/swebench` +
   `--grader-on-unavailable` plumbing are deleted.

## Rationale
The directive demands a genuine coding agent and zero Docker; the agentic loop +
host venv is the only option that delivers both *and* is fully provable offline
(stub repo + injected git/command/CLI runners). The non-negotiable that keeps the
numbers trustworthy is the `success=None` trust boundary: the model can edit code
but cannot grade itself, and the harness — not the agent — applies the gold tests.

## Tradeoffs & risks
- **Not leaderboard-comparable.** Host-local execution is host-dependent and
  partial-coverage; numbers from it must NOT be compared to a containerized
  SWE-bench leaderboard. This is the real cost of dropping Docker and is stated in
  every user-doc touching CODE grading.
- **Multilingual SWE-ContextBench is largely un-gradeable on one host** (51 repos,
  9 languages, per-repo toolchains). Those instances return `None` (ungraded) and
  drop out of the accuracy denominator rather than scoring a false `False`.
- **Real runs need network + auth + a buildable repo.** Offline tests prove the
  loop with a stub repo + fake runners only; a real swe_contextbench run needs a
  GitHub fetch-by-SHA, live `claude` subscription auth, and a repo whose env builds.
  Mitigation: the `None`-on-failure honesty rule means an unbuildable env never
  corrupts the reported accuracy.
- **Reproducibility drifts with the host.** Mitigated by pinning `base_commit`,
  applying gold tests from the dataset, and recording the resolved rule explicitly.

## Consequences for the build
- **Policy:** the agentic CODE path MUST return `success=None` — the grader owns
  the verdict; an agent that self-grades CODE is a bug.
- **Policy:** any inability to build the env or run the tests grades to `None`
  (UNGRADED), never `False` and never an exception that aborts the run.
- **Policy:** the harness applies the gold `test_patch`; the agent never touches
  the tests (the trust boundary).
- **Policy:** no Docker / `swebench` references in code, config, or user-docs.
  Real CODE scoring needs no extra install; `--grader auto` picks `local` for the
  SWE benches and `None` for QA + contextbench.
- **Scope:** the team's memory mechanism is reused unchanged
  (`eval/memeval/stores/**`, `okf.py`, `router.py`, `protocols.py`,
  `claudecode/service.py`, `claudecode/memory_server.py`, the harness scorer); the
  only memory-side edit is a pure extract-to-helper refactor of the plugin
  seed+attribute steps so the CODE path can call them, guarded by existing tests.
- **Frozen contract untouched:** `schema.py` already carries every CODE field and
  `AgentResult(prediction, patch, success)`; no schema change.
