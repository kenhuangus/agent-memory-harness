---
id: ADR-dreaming-018
domain: dreaming
title: CLI argparse-error exit code 1 (not 2) — Claude Code reserves exit 2 for hook-blocking
status: Accepted
date: 2026-06-21
contract: true
supersedes: ADR-dreaming-016
superseded_by: none
owner: Scott B. (P4)
origin: design session 2026-06-21 (halliday F1 — Claude Code plugin hooks doc fetch)
---

# ADR-dreaming-018: CLI argparse-error exit code is `1` (not `2`) — Claude Code reserves exit 2 for hook-blocking

**Status:** Accepted · **Date:** 2026-06-21 · **Contract:** yes
**Supersedes:** [`ADR-dreaming-016`](ADR-dreaming-016-rename-memory-cli-to-daydream-cli.md) (partial — exit-code policy only) · **Superseded by:** none

> **Scope of supersession.** This ADR replaces ONLY the exit-code
> policy carried forward from
> [`ADR-dreaming-008`](ADR-dreaming-008-memory-cli-console-script.md)
> through
> [`ADR-dreaming-016`](ADR-dreaming-016-rename-memory-cli-to-daydream-cli.md):
> the "Exit codes: `0` = success, `2` = argument error" line in
> §Consequences. All other decisions in ADR-016 (entry-point name
> `daydream-cli`, subcommand pattern, standalone console script,
> `daydream` extra) stand unchanged. ADR-016 remains the durable
> record of the rename + naming rationale; this ADR is layered on
> top, narrow.

## Context

ADR-008 (carried forward by ADR-016) pinned `2` as the CLI's
argparse-error exit code, matching Python's stdlib argparse default.
This was safe in isolation. PR5 wires the CLI as the shell-out target
for the Claude Code plugin's `Stop` and `PreCompact` hooks
([`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md)), and
the CC plugin hooks contract — fetched from
https://code.claude.com/docs/en/hooks on 2026-06-21 — reserves
exit-code `2` for a specific signal:

> **Exit code 2 (blocking error).** stderr text is fed back to Claude
> as an error message. The effect depends on the event: for `Stop`,
> exit 2 *prevents Claude from stopping; continues the conversation*.
> For `PreCompact`, exit 2 *blocks compaction*.

This collides directly with argparse's stdlib default. If
`daydream-cli daydream` is invoked from a hook with a bad
flag — say, after a future CLI refactor renames a flag the manifest
still passes — argparse exits `2`, CC interprets it as "block the
hook's action," and the user's session enters an unbounded
non-stoppable loop or a no-compact silent-degrade state. The failure
mode is catastrophic for a contract that exists only to fail-open
(per [`ADR-harness-006`](ADR-harness-006-fail-open.md)).

The collision was not visible at ADR-008's authoring time because the
plugin shim hadn't been wired; PR5 is the first PR where the CLI is a
hook-consumed surface.

## Options considered

- **Remap argparse errors to exit `1`** (chosen). Use
  `ArgumentParser(exit_on_error=False)` and a try/except `SystemExit`
  at the `main()` boundary that catches the SystemExit raised by
  argparse on parse failure, emits its stderr message verbatim, and
  returns `1`. (Alternative implementation: subclass
  `ArgumentParser` and override `.error()` to call `sys.exit(1)`.) Exit
  code `1` is a generic "non-blocking error" per CC's spec; CC shows
  stderr in the transcript but does not block the hook's action.
- Keep argparse default `2`, document it as a known footgun. Rejected:
  the failure mode is silent-and-catastrophic (Stop hook refusing to
  let Claude finish a turn). A documented footgun does not constitute
  a defense.
- Pick exit code `3` or higher. Functionally equivalent to `1` per the
  CC spec (anything not `0` or `2` is treated identically). `1` is the
  canonical Unix "general error" — least cognitive overhead for
  readers.
- Make the CLI never error on parse — accept any flags, no-op on
  unknown ones. Rejected: silently accepting bad flags removes the
  feedback that catches the kind of refactor mistake this exit-code
  collision exists to surface.

## Decision

- **Argparse-error exit code is `1`.** The CLI MUST NOT return `2`
  from `main()` for any reason. Implementation: wrap argparse with
  `exit_on_error=False` or override `.error()` so any parse failure
  routes to `return 1` from `main()`.
- **Success exit code remains `0`**, including fail-open exit paths
  per [`ADR-harness-006`](ADR-harness-006-fail-open.md). Unchanged.
- The PR5 rubric pins this contract verbatim (criterion 18 forbids
  `sys.exit(2)` / `return 2` anywhere in `cli.py`; criteria 15–17
  + 23–28 assert exit `1` on each argparse-error case).

## Rationale

Exit `1` is a domain transposition: the stdlib default `2` collides
with a load-bearing CC plugin signal, so the CLI uses the next-lowest
generic "error" code that CC's spec treats as informational
(stderr shown to user; execution continues). Stdlib divergence is
intentional — the CLI's primary consumer (a CC hook) overrules
stdlib convention.

Centralizing the remap at `main()` (rather than overriding argparse
deep) makes the override visible to a reader: `main()` is the
single entry point; the wrap is a 3-line try/except at the top of
its body. Future maintainers see the exit-code policy at the place
they'd look for it.

## Tradeoffs & risks

- **Stdlib divergence is mild surprise.** A reader expecting
  argparse's default `2` on bad args sees `1` instead. Mitigated by
  the docstring on `main()` and by this ADR. Acceptable cost for
  closing the catastrophic failure mode.
- **Manual `daydream-cli` invocations** (developer typing in a shell)
  also exit `1` on bad args — diverges from the convention of every
  other CLI on the developer's machine. Acceptable: developers read
  stderr; the failure is informational either way; the cost of
  divergence is offset by the certainty that the plugin path is safe.
- **`KeyboardInterrupt` and `SystemExit` continue to propagate**
  (PR5 rubric criteria 42–43 + 51–52). Unchanged from ADR-008's
  carry-forward.
- **If CC's spec evolves and reassigns exit-code semantics**, this
  ADR's reasoning is invalidated. The `sha256` pins on the manifest
  hooks (PR5 rubric criteria 74–75) catch drift in the *manifest*; a
  spec change is harder to detect, but the
  [`daydream.cli_resolved`](ADR-dreaming-009-events-shim.md) event
  emitted on every invocation (PR5 criterion 36) gives the operator
  the package version + script path in the diary, so a regression can
  be traced.

## Consequences for the build

- **Contract — source of truth:** `eval/memeval/dreaming/cli.py`
  `main()` — wraps argparse parsing in a try/except SystemExit (or
  uses `exit_on_error=False`) and returns `1` on parse failure.
- **Shape:**
  - `daydream-cli --help` → exit `0`.
  - `daydream-cli` (no subcommand) → exit `1` + stderr usage.
  - `daydream-cli bogus` → exit `1` + stderr usage.
  - `daydream-cli daydream --unknown-flag X` → exit `1` + stderr.
  - `daydream-cli daydream …` with valid args, engine succeeds →
    exit `0`.
  - Any engine exception (fail-open per ADR-harness-006) → exit `0`.
- **Policy — never return `2`.** Source-scan: `cli.py` MUST NOT
  contain the literal `sys.exit(2)` or `return 2`. PR5 rubric
  criterion 18 enforces.
- **Exhaustive consumers:** the Claude Code plugin `Stop` and
  `PreCompact` hook commands (which interpret exit `2` as
  "block this action"), and the manual developer shell invocation
  (which sees the stderr message and the non-`0` exit).

## Open items

- **ADR-001/002 literal-text references** to `memory daydream` /
  `memory dream --all` (already stale per ADR-016 §Open items) remain
  stale. Not addressed here.
- **Stdlib divergence linting** — no automated check currently warns a
  future contributor that `sys.exit(2)` is forbidden in this module.
  PR5 rubric criterion 18 catches it at test time; a linter rule
  would catch it earlier. Not scheduled.
