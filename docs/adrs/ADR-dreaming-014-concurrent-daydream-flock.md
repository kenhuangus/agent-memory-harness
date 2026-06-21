---
id: ADR-dreaming-014
domain: dreaming
title: Concurrent Daydream invocations — flock per session_id + idempotent exit-0
status: Accepted
date: 2026-06-21
contract: true
supersedes: none
superseded_by: none
owner: Scott B. (P4) — engine; Keith (P1) — informed (plugin hook scope)
origin: design session 2026-06-21 (halliday adversarial-review Finding #4)
---

# ADR-dreaming-014: Concurrent Daydream invocations — `flock` per `session_id` + idempotent exit-0

**Status:** Accepted · **Date:** 2026-06-21 · **Contract:** yes
**Supersedes:** none · **Superseded by:** none

## Context
[`ADR-dreaming-007`](ADR-dreaming-007-stop-hook-driven-turn-cursor.md)
listed "one Daydream process per session log assumed" as an Open item.
Halliday (Finding #4, HIGH) named real concurrency vectors:

- **Same session_id, concurrent fires** — `Stop` + `PreCompact` firing
  close together; rapid Stop fires (plugin glitch).
- **Different session_ids, concurrent fires** — multiple sub-agents
  finishing in parallel (each gets its own `SubagentStop` event with its
  own session_id and transcript_path, verified 2026-06-21 at
  https://code.claude.com/docs/en/hooks); eval driver running N
  benchmarks in parallel; user runs `claude -p` in two terminals.

The sidecar JSON is read-modify-write
([`ADR-harness-004`](ADR-harness-004-dream-state-sidecar.md)); lost-update
is the trivial outcome for same-session concurrency. "Assumed" is not
enforced.

## Options considered
- **Non-blocking `flock` per `<basedir>/dream/<session_id>.lock` +
  exit-0 silently when the lock is held** (chosen) — protects against
  same-session corruption without serializing legitimate parallel
  different-session work; matches
  [`ADR-harness-006`](ADR-harness-006-fail-open.md) fail-open.
- Explicit forbid + named-error exit-2 — visible but contradicts
  fail-open in plugin contexts that treat non-zero as failure.
- Global lock (one Daydream at a time across all sessions) — serializes
  legitimate parallel sub-agent work for no safety benefit; same-session
  protection doesn't require global serialization.
- No locking — accepts the race; halliday flagged HIGH.

## Decision
**Every Daydream invocation:**

1. Compute `lock_path = <basedir>/dream/<session_id>.lock`.
2. Attempt `flock(lock_path, LOCK_EX | LOCK_NB)` (non-blocking exclusive
   advisory lock).
3. **If acquisition fails** (lock already held by another Daydream
   process for the same session_id):
   - `emit("concurrent_daydream_skipped", session_id=...)` via the
     [`ADR-dreaming-009`](ADR-dreaming-009-events-shim.md) shim.
   - Exit 0 immediately.
4. **If acquisition succeeds:**
   - Process the chunk per
     [`ADR-dreaming-013`](ADR-dreaming-013-cursor-advance-ordering.md).
   - Release the lock on normal completion AND in the exception path
     (use a context manager so `__exit__` always runs).

**Lock granularity is per `session_id`.** Different `session_id`s →
different lock files → no serialization. SQLite WAL in the store handles
concurrent writes across sessions per
[`ADR-storage-001`](ADR-storage-001-orchestrator-in-process-library.md).

**Verified scope of "different session_ids":** per Claude Code's hook
docs, each subagent has its own `session_id` and `transcript_path`;
`SubagentStop` is a separate event from `Stop`. So N concurrent
sub-agents finishing simultaneously generates N parallel Daydream
processes against N session-id-distinct lock files — all run in
parallel, none serialize.

## Rationale
The threat is sidecar corruption from same-session concurrent
read-modify-write. Per-session locking matches that threat exactly
without paying parallelism cost on the common case (different sessions
running concurrently). Idempotent exit-0 matches the fail-open posture
and combines naturally with ADR-013's cursor-not-advanced-on-exception:
if a Daydream skipped due to lock, the next Stop fire processes the
unchanged cursor's content — no data lost.

## Tradeoffs & risks
- **Lock files accumulate** in `<basedir>/dream/` — one `<session>.lock`
  per session ever seen. Bounded growth by session count, not turn
  count; paired with ADR-009/ADR-011 retention work.
- **Hung Daydream process holding lock** = subsequent invocations skip
  forever for that session. Open item below: stale-lock detection.
- **Cross-host (NFS / network filesystems)** — POSIX `flock` semantics
  differ on networked filesystems; v1 assumes local filesystem (matches
  `$MEMORY_STORE` deployment assumption).
- **Plugin hook-scope choice is harness-domain** — see below.

## Consequences for the build
- **Contract — source of truth:** `daydream(*, session_id, log_path,
  store, basedir)` entrypoint in the dreaming package.
- **Shape:**
  ```python
  import fcntl
  from contextlib import contextmanager
  from pathlib import Path

  @contextmanager
  def _per_session_lock(basedir: Path, session_id: str):
      lock_path = basedir / "dream" / f"{session_id}.lock"
      lock_path.parent.mkdir(parents=True, exist_ok=True)
      fp = open(lock_path, "w")
      try:
          fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
      except BlockingIOError:
          fp.close()
          emit("concurrent_daydream_skipped", session_id=session_id)
          raise _LockHeld()  # caller exits 0
      try:
          yield
      finally:
          fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
          fp.close()
  ```
- **Policy — lock release on success AND exception** (context manager's
  `__exit__` always runs).
- **Policy — emit `concurrent_daydream_skipped` event** when the lock
  is held; never raise to the caller.
- **Policy — same-session serialization, cross-session parallelism**
  is the intended behavior, not a side effect.
- **Cross-domain — plugin hook scope (Keith):** the plugin
  ([`ADR-harness-001`](ADR-harness-001-claude-code-plugin-shape.md))
  chooses whether to register `Stop` only, `SubagentStop` only, or
  both. Affects how often parallel Daydreams fire, NOT engine
  correctness. Engine handles all three configurations identically.

## Open items (dreaming-owned + cross-domain)
- **Stale-lock detection:** if a Daydream process dies while holding
  the lock, subsequent invocations skip forever. PR follow-up: write a
  PID/timestamp to the lock file; on `BlockingIOError`, check if the
  named PID is still alive; if not, take the lock with a `stale_lock_
  reclaimed` event.
- **Cross-host concurrency** (`flock` on NFS) — out of v1 scope; v1
  assumes local filesystem.
- **Plugin hook-scope decision:** coordinate with Keith on which of
  `Stop` / `SubagentStop` / both to register, per coverage goals.
  Engine is agnostic.
- **Lock-file retention** — paired with ADR-009/ADR-011 retention
  policies.
