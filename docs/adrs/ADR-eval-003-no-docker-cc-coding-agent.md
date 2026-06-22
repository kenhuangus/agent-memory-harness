---
id: ADR-eval-003
domain: eval
title: Docker removed entirely — Claude Code CLI is the coding agent; LocalExecGrader / retrieval replace the SWE-bench Docker grader
status: Accepted
date: 2026-06-22
contract: false
supersedes: ADR-eval-002
superseded_by: none
owner: Ken (P2)
origin: user directive (overrides the design doc's "keep Docker opt-in")
---

# ADR-eval-003: Docker removed entirely — Claude Code CLI is the coding agent; LocalExecGrader / retrieval replace the SWE-bench Docker grader

**Status:** Accepted · **Date:** 2026-06-22 · **Contract:** no
**Supersedes:** ADR-eval-002 · **Superseded by:** none

## Context
ADR-eval-002 ("Docker-free CODE grading") moved CODE solving to an agentic
Claude Code loop and CODE grading to a host-local `LocalExecGrader`, and stated
that the SWE-bench Docker grader, the `swebench` extra, and the Docker plumbing
were deleted from the code. What it did **not** do is finish the job in the
surrounding documentation and configuration: the user-facing docs, the PRD, the
benchmark pages, the protocol, the per-developer README, the results report, and
a handful of code/test comments still mentioned Docker and the `swebench`
package — framing Docker as a thing one might still reach for, or pointing at a
container-based grader as the reference path.

The user directive is explicit and stronger than the design doc's original
"keep Docker opt-in" stance: **Docker must be removed ENTIRELY.** There is to be
no `SWEBenchDockerGrader`, no `swebench` extra, and no Docker references anywhere
in code, configuration, or user-facing documentation. The Claude Code CLI must
be the genuine coding agent. This ADR records the decision that completes that
removal at the documentation/config layer and pins the project's single,
container-free description of the CODE pipeline.

## Decision
1. **Docker is removed entirely — not opt-in, not a fallback, not a footnote.**
   Every remaining Docker / `swebench`-package reference outside the historical
   ADRs is scrubbed from `eval/pyproject.toml` (the `swebench` extra and its
   comments), `README.md`, `eval/README.md`, `eval/memeval/claudecode/README.md`,
   `results/v0.1/README.md`, `benchmarks.html`, `prd.md`, `eval/PROTOCOL.md`, and
   the residual code/test comments (`eval/memeval/grader.py`,
   `eval/tests/test_smoke.py`, `eval/tests/test_claudecode_code_agent.py`,
   `eval/tools/_measure.sh`).
2. **The Claude Code CLI is the coding agent.** CODE benchmarks are solved by
   driving `claude` as a real software engineer in a fresh working checkout of the
   task's repo at `base_commit` (`--code-mode agentic`, the default): it reads and
   edits the source with its native tools and runs the tests; the harness captures
   `git diff` as the prediction. This is a genuine checkout/edit/run loop, not a
   one-turn "emit a diff" prompt.
3. **`LocalExecGrader` and retrieval metrics replace `SWEBenchDockerGrader`.**
   - `swe_contextbench` / `swe_bench_cl` → `LocalExecGrader`: provision a fresh
     checkout, apply the agent's prediction, apply the **gold `test_patch`** (the
     harness applies the tests, never the agent — the trust boundary), build a
     per-task venv best-effort, and run `FAIL_TO_PASS` + `PASS_TO_PASS` on the
     host. RESOLVED follows the SWE-bench rule.
   - `contextbench` → **retrieval-only**: scored by its native
     recall/precision/F1 over gold spans, no test execution.
   - QA benchmarks grade by normalized exact match. No grader involves a
     container runtime or an external grading package.
4. **`success=None` trust boundary kept.** The agentic CODE solve returns
   `AgentResult(success=None)` so the harness grader — never the model — owns the
   verdict, and any environment that can't be built grades to `None` (UNGRADED,
   excluded from accuracy), never a fake `False`.
5. **Single source of truth for the description.** All docs now describe one
   container-free pipeline; this ADR (not ADR-eval-002, which it supersedes) is
   the canonical reference the docs link to.

## Rationale
ADR-eval-002 made the right architectural call but left the public surface area
half-migrated, which is exactly how a "removed" dependency creeps back: a reader
who finds a `swebench` extra in `pyproject.toml` or "no Docker daemon needed"
phrasing in a README reasonably infers Docker is still a supported mode. Removing
the dependency in code but leaving it in the docs is not removing it. Finishing
the scrub and naming the Claude Code CLI as *the* coding agent makes the
container-free, agent-driven design the only thing the project describes.

## Tradeoffs & risks (reproducibility trade-off)
- **Not official SWE-bench numbers / not leaderboard-comparable.** Host-local
  execution is host-dependent and only partial-coverage; numbers it produces MUST
  NOT be compared to a containerized SWE-bench leaderboard. This reproducibility
  loss is the deliberate, accepted cost of removing Docker entirely.
- **Multilingual SWE-ContextBench is largely un-gradeable on a single host**
  (many repos, many language toolchains). Those instances return `None`
  (ungraded) and drop out of the accuracy denominator rather than scoring a false
  `False`. The `success=None` honesty rule is what keeps the reported accuracy
  trustworthy despite partial coverage.
- **Real runs still need network + subscription auth + a buildable repo.**
  Offline tests prove the loop with a stub repo + injected git/command/CLI
  runners only.

## Consequences
- **Policy:** no Docker / `swebench`-package references in code, config, or
  user-docs. The only legitimate remaining mentions of "docker"/"swebench" are
  (a) the historical ADRs under `docs/adrs/` (append-only — ADR-eval-002 records
  the original removal decision and is preserved verbatim), (b) this ADR, which
  explains the complete removal, and (c) the benchmark's own canonical name —
  `SWE-Bench-CL` / `SWEBenchCLLoader` and the frozen `schema.py` alias
  `"swebench_cl"` — which name a dataset we load, not the Docker grading package.
- **Policy:** the harness applies the gold `test_patch`; the agent never touches
  the tests, and the agentic CODE path returns `success=None`.
- **Scope:** the team's memory mechanism is reused unchanged
  (`eval/memeval/stores/**`, `okf.py`, `router.py`, `protocols.py`,
  `claudecode/service.py`, `claudecode/memory_server.py`, the harness scorer).
- **Frozen contract untouched:** `eval/memeval/schema.py` is unchanged — it
  already carries every CODE field and the benchmark-name aliases.
- **Supersedes ADR-eval-002.** ADR-eval-002 remains the historical record of the
  original architectural decision; this ADR is the current reference for the
  fully Docker-free, Claude-Code-CLI-driven CODE pipeline.
