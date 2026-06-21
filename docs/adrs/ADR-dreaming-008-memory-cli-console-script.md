---
id: ADR-dreaming-008
domain: dreaming
title: memory CLI is a standalone console script in eval/memeval/dreaming/cli.py
status: Superseded
date: 2026-06-21
contract: true
supersedes: none
superseded_by: ADR-dreaming-016
owner: Scott B. (P4)
origin: design session 2026-06-21 (Daydream PR1 gap pass)
---

# ADR-dreaming-008: `memory` CLI is a standalone console script in `eval/memeval/dreaming/cli.py`

**Status:** Superseded · **Date:** 2026-06-21 · **Contract:** yes
**Supersedes:** none · **Superseded by:** [`ADR-dreaming-016`](ADR-dreaming-016-rename-memory-cli-to-daydream-cli.md)

## Context
[`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md) specifies
the plugin invokes Daydream as `memory daydream --session <id> --log
<transcript_path> [--store P]`.
[`ADR-dreaming-002`](ADR-dreaming-002-dreaming-consolidation-cli.md)
specifies night consolidation as `memory dream --all`. Both ADRs reference a
`memory` CLI that does not yet exist in the tree — the only console script
today is `memeval = "memeval.cli:main"` (the benchmark harness CLI).

The plugin (harness-domain, Keith's lane) shells out to `memory` —
Daydream's CLI is therefore an **invocation contract** consumed by another
workstream.

## Options considered
- **Standalone console script `memory` in
  `eval/memeval/dreaming/cli.py`** (chosen) — matches ADRs 001 and 002 text
  literally; clean dreaming-domain ownership; plugin shells out without
  coupling to `memeval`'s eval-only concerns.
- Subcommand under existing `memeval` CLI (`memeval memory daydream ...`) —
  reuses one entry point but turns ADRs 001/002 text into aspirational
  naming. Reviewer / new-contributor friction.
- Plugin owns the CLI (`eval/memeval/claudecode/cli.py`) — couples Daydream
  invocation to the harness adapter, contradicts ADR-001's
  isolated-entrypoint framing.

## Decision
- Add a second console_scripts entry to `eval/pyproject.toml`:
  `memory = "memeval.dreaming.cli:main"`.
- Create `eval/memeval/dreaming/cli.py` with an argparse-based `main()` and
  two sub-commands:
  - `memory daydream --session <id> --log <transcript_path> [--store P]` →
    calls `daydream(...)` per
    [`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md).
  - `memory dream --all [--store P]` → calls the night-scope consolidation
    per [`ADR-dreaming-002`](ADR-dreaming-002-dreaming-consolidation-cli.md).
- Argparse, matching the convention of `eval/memeval/cli.py`.

## Rationale
The CLI invocation is what other workstreams depend on; making the literal
text in ADRs 001/002 match the literal shell invocation is the lowest-friction
contract. A standalone entry point keeps dreaming's CLI distinct from
`memeval`'s benchmark CLI — they have unrelated scopes, and folding them
would force `memeval` users to read past `memory` subcommands they don't use
(and vice versa).

## Tradeoffs & risks
- **Two console scripts to maintain** (`memeval`, `memory`). Minor; their
  surfaces don't overlap.
- **"memory" is a generic name** that may collide with other tools on
  PATH. Acceptable for a personal-machine eval artifact; the project's
  install footprint is opt-in (`pip install -e eval[daydream]`), not
  global.
- **Cross-workstream invocation contract.** Plugin (Keith) and Daydream
  CLI (Scott) coordinate on the exact flag names + exit codes. Changes
  require informing Keith.

## Consequences for the build
- **Contract — source of truth:** `eval/memeval/dreaming/cli.py` defines
  the `memory` command shape (sub-commands, flags, exit codes).
- **Shape:**
  - `memory daydream --session <id> --log <path> [--store <path>]` →
    `daydream(session_id=..., log_path=..., store=...)`.
  - `memory dream --all [--store <path>]` → `dreaming.run(store=...)`.
  - Exit codes: `0` = success (including fail-open no-ops),
    `2` = argument error. Never raise to a non-zero exit on consolidation
    failure, per [`ADR-harness-006`](ADR-harness-006-fail-open.md).
- **Policy — pyproject.toml:**
  ```toml
  [project.scripts]
  memeval = "memeval.cli:main"
  memory  = "memeval.dreaming.cli:main"
  ```
- **Exhaustive consumers:** the Claude Code plugin's Stop/PreCompact hook
  (which shells out via `memory daydream ...` per ADR-dreaming-001), and
  whatever ops surface invokes `memory dream --all` for night
  consolidation (ADR-dreaming-002).
- **Policy — install requirement:** the `daydream` extra in
  `eval/pyproject.toml` is required for `memory` to be on PATH (it's the
  same extra that pulls `detect-secrets`).

## Open items (dreaming-owned)
- **Subcommand surface beyond v1** — e.g. `memory daydream --replay`
  (future replay path per
  [`ADR-dreaming-007`](ADR-dreaming-007-stop-hook-driven-turn-cursor.md)
  open items) and `memory dream --inspect` (dump store contents) — flagged
  as additions, not blockers.
