---
id: ADR-dreaming-017
domain: dreaming
title: PR5 plugin-shim operational contract — PreCompact silent-skip on Stop concurrency + transcript-path trust model
status: Accepted
date: 2026-06-21
contract: true
supersedes: none
superseded_by: none
owner: Scott B. (P4)
origin: design session 2026-06-21 (halliday adversarial-review of PR5 plan — findings F2 + F3)
---

# ADR-dreaming-017: PR5 plugin-shim operational contract — PreCompact silent-skip on Stop concurrency + transcript-path trust model

**Status:** Accepted · **Date:** 2026-06-21 · **Contract:** yes
**Supersedes:** none · **Superseded by:** none

> **Scope.** This ADR adds two operational observations that PR5's
> adversarial review surfaced. It does NOT supersede
> [`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md) —
> its Decision (Daydreaming fires automatically on `Stop`/`PreCompact`)
> is unchanged. This ADR documents two consequences observed while
> wiring the plugin shim that bind every PR5+ consumer: the
> PreCompact-fired pass may silently no-op when a `Stop`-fired pass is
> still holding the per-session `flock` (ADR-014), and the CLI treats
> `transcript_path` (CC stdin) and `--log` (manual override) as
> trusted inputs.

## Context

PR5 wires the Claude Code plugin to Daydreaming via two hooks (`Stop`
async, `PreCompact` synchronous) per
[`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md). Two
non-obvious behaviors emerged during the adversarial review of the PR5
plan (halliday F2 + F3) that need a durable contract record before
implementation:

1. **Stop/PreCompact concurrency.** The engine uses
   `LOCK_EX | LOCK_NB` per
   [`ADR-dreaming-014`](ADR-dreaming-014-concurrent-daydream-flock.md) so
   concurrent invocations against the same `session_id` early-return
   with `_LockHeld` rather than wait. When `PreCompact` fires while
   `Stop`'s `async: true` pass is still running (long extraction, slow
   LLM, large chunk), `PreCompact` hits the held lock and silently
   skips. ADR-001 names PreCompact as "a final pre-compaction pass" —
   a silent skip means the last chunk before compaction is
   unprocessed *by this hook firing*. Crucially, the cursor is NOT
   advanced (per
   [`ADR-dreaming-013`](ADR-dreaming-013-cursor-advance-ordering.md)),
   so the next Stop pass catches up. The behavior is correct (no data
   loss) but operationally invisible — there is no event emitted to
   distinguish "PreCompact skipped because Stop was running" from
   "PreCompact had nothing to do."

2. **Transcript-path trust.** The CLI receives `transcript_path` from
   the CC plugin hook's stdin JSON (per
   [`ADR-dreaming-018`](ADR-dreaming-018-cli-argparse-exit-code.md), which
   pins the stdin-JSON contract) or `--log` from manual invocation.
   The engine `open()`s that path raw — no prefix allowlist, no
   symlink resolution, no workspace containment. A hostile or
   misconfigured transcript path (symlink to `/etc/passwd`, path
   outside the workspace, malicious file substituted between hook
   fire and engine read) would be read, redacted incompletely (PR1's
   `detect-secrets` covers tokens/keys, not arbitrary file contents
   like env-file values or `.gitconfig` emails), and pushed to the
   LLM. The implicit v1 trust model — "CC is trusted to provide a
   real transcript path" — has not been stated as a contract.

## Options considered

**(1) PreCompact concurrency — what to do when Stop holds the lock:**

- **Accept the silent skip (chosen).** Keep PR4's `LOCK_NB` semantics;
  PreCompact early-returns; the next Stop pass catches up since the
  cursor isn't advanced. Add a follow-up open item for an explicit
  event (`daydream.precompact_skipped_stop_running`) when the engine
  next ships changes. **Zero PR5 engine changes** (scope discipline:
  PR5 rubric criterion 75 forbids edits to `engine.py`).
- Switch PreCompact to `LOCK_EX` (blocking). Requires an engine-side
  mode flag plumbed through the CLI. Crosses scope into PR4-frozen
  territory. The blocking call could also hang PreCompact past CC's
  per-hook timeout (default 600s in PR5).
- Defer PreCompact entirely on Stop-in-flight via a CLI-side
  pre-check (read the lock file's PID, decide whether to invoke the
  engine). Replicates engine logic in the CLI; couples CLI to
  lock-file format; brittle.

**(2) Transcript-path trust — how strict is the CLI/engine boundary:**

- **Accept v1 personal-machine trust model (chosen).** Document that
  `transcript_path` / `--log` are trusted inputs; CC is trusted to
  provide a real transcript path; hostile substitution is in-scope
  for the threat model but accepted for v1. Hardening deferred. Zero
  PR5 code changes.
- Path-prefix allowlist (only paths under
  `~/.claude/projects/*/` accepted). Closes the obvious foot-gun;
  costs a hardcoded prefix that may not generalize across CC's
  install layouts (Linux vs macOS vs Windows; pipx vs npm vs native
  binary). Mostly false-positives or mostly false-negatives, no
  middle ground.
- Symlink resolution + `O_NOFOLLOW`. Catches symlink races at the
  cost of a TOCTOU window between resolve and open. Real defense
  needs both — significantly more code.

## Decision

**(1) PreCompact concurrency.** PR5 ACCEPTS the silent
PreCompact-skip when a Stop-fired daydream is still holding the
per-session `flock`. The behavior is safe (cursor not advanced; next
Stop catches up). Operational visibility is deferred: a follow-up PR
adds a `daydream.precompact_skipped_stop_running` event at the
engine's `_LockHeld` early-return path.

PR5's pull-request description MUST contain a "Known limitation"
section naming this behavior and linking to this ADR (PR5 rubric
criterion 93 enforces).

**(2) Transcript-path trust.** PR5 CODIFIES the v1 trust model:
`transcript_path` (CC stdin) and `--log` (manual override) are
trusted inputs. The engine reads them raw — no prefix allowlist, no
symlink resolution, no workspace containment. Hostile or
misconfigured paths are out-of-scope for v1 hardening; in-scope for
the threat model (a malicious actor with shell access can already
read the same files). Hardening (path-prefix allowlist or
`O_NOFOLLOW`) is deferred to a future ADR when the plugin moves
beyond personal-machine eval.

## Rationale

**On (1).** The engine's existing lock-and-skip is correct: it
preserves the cursor-advance invariant
([`ADR-013`](ADR-dreaming-013-cursor-advance-ordering.md)) and
guarantees idempotence
([`ADR-014`](ADR-dreaming-014-concurrent-daydream-flock.md)). The
worst case is a bounded one-hook-fire delay before the last
pre-compaction chunk is processed — the next Stop catches up. Adding
a blocking acquire would risk hanging the synchronous PreCompact past
CC's hook timeout, which is a strictly worse failure mode (the hook
aborts mid-flight instead of cleanly no-op-ing). Adding the event
emission requires touching `engine.py`, which PR5 explicitly scopes
out. Deferring the event to a follow-up PR pays the operational-cost
once, in the right place.

**On (2).** The v1 audience is personal-machine eval. Hardening the
transcript-path boundary now costs concrete code (and likely a
platform-specific allowlist that won't survive a CC layout change)
against a threat (malicious local file substitution) that requires
shell access — and a shell-access actor can already read the same
files directly. The cost/value is wrong for v1. Codifying the trust
model now (rather than leaving it implicit) lets a future hardening
PR start from a clear contract instead of having to re-derive it.

## Tradeoffs & risks

- **Silent PreCompact skip is operationally invisible until the
  follow-up event ships.** A user inspecting their diary cannot
  distinguish "PreCompact ran and found nothing new" from "PreCompact
  hit the lock and silently no-op'd." Mitigation: the follow-up event
  is small (≤10 lines in `engine.py` `_LockHeld` handler) and tracked
  as an open item below.
- **The "next Stop catches up" invariant depends on the cursor not
  being advanced by PreCompact's no-op path.** PR4 + ADR-013 already
  guarantee this; criterion 91 (no regressions in PR1–PR4 tests)
  protects against drift.
- **Trust-model codification could be read as license for laxness in
  later layers.** Counter-explicit in §Decision: this is a v1
  carve-out for personal-machine use, not a license to skip
  validation when the surface broadens.
- **Hostile transcript-path scenarios that the redaction layer
  doesn't cover (env files, `.gitconfig`, etc.) become silently
  pushed to the LLM if CC's stdin payload is compromised.** Real
  risk; v1 accepts. Mitigation: the
  [`daydream.cli_resolved`](ADR-dreaming-009-events-shim.md) event
  (PR5 rubric criterion 36) records the resolved transcript-path in
  the diary so a post-incident audit can identify what was read.

## Consequences for the build

- **Contract — source of truth (operational behavior):**
  `eval/memeval/dreaming/_state.py:_per_session_lock` (lock-on-flock
  semantics) + `eval/memeval/dreaming/engine.py:daydream` (the
  `_LockHeld` catch + early-return; UNCHANGED by PR5).
- **Policy — PR5 plugin shim** does NOT add any concurrency-control
  beyond what the engine already does. It does NOT add path
  validation on `--log` / stdin `transcript_path`.
- **Policy — PR5 rubric** enforces this contract: criterion 93 (PR
  description "Known limitation" section) + the §X carve-outs for
  PreCompact silent-skip and transcript-path trust.
- **Exhaustive consumers:** the Claude Code plugin `Stop` / `PreCompact`
  hooks (the trigger surfaces), the `daydream-cli daydream` shim (the
  shell-out boundary), and any operational tooling that surfaces
  Daydream events to a human (none yet, but the open-item event
  emission is in this consumer set).

## Open items (dreaming-owned)

- **Follow-up PR: `daydream.precompact_skipped_stop_running` event.**
  Engine-side change at the `_LockHeld` early-return in
  `engine.daydream`. Includes a structured field naming the lock-
  holder PID (if recoverable) so an operator can distinguish "Stop is
  still running" from "stale lock." Owner: Scott B.; scheduled after
  PR5.
- **Future ADR: transcript-path hardening when the plugin moves
  beyond personal-machine eval.** Pre-conditions: a real
  multi-machine distribution channel; a documented CC transcript
  storage layout (Linux + macOS + Windows). Approach options to weigh
  at that time: path-prefix allowlist, `O_NOFOLLOW` + symlink-resolve
  + recheck, or relocating the engine into a sandbox process. Not
  scheduled.
- **ADR-001 cross-reference.** When the operational event ships, the
  decision-index entry for ADR-001 should add a brief "extended by
  ADR-017" note; no body edit (per the README rule).
