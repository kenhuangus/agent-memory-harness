---
id: ADR-dreaming-013
domain: dreaming
title: Cursor-advance ordering — memories-then-cursor, atomic sidecar write, no advance on exception
status: Accepted
date: 2026-06-21
contract: true
supersedes: none
superseded_by: none
owner: Scott B. (P4)
origin: design session 2026-06-21 (halliday adversarial-review Finding #3)
---

# ADR-dreaming-013: Cursor-advance ordering — memories-then-cursor, atomic sidecar write, no advance on exception

**Status:** Accepted · **Date:** 2026-06-21 · **Contract:** yes
**Supersedes:** none · **Superseded by:** none

## Context
[`ADR-dreaming-007`](ADR-dreaming-007-stop-hook-driven-turn-cursor.md)
specifies cursor → EOF per Stop-hook invocation, and "after processing,
Daydream writes `sidecar.cursor = current_eof`."
[`ADR-harness-004`](ADR-harness-004-dream-state-sidecar.md) specifies the
sidecar shape.

Neither ADR specifies the **order** of operations across
`{redact → LLMClient.complete → orchestrator.write(memories) →
sidecar.cursor write}`. Halliday (Finding #3, HIGH) named the three
failure modes:

- **(a) Cursor first → crash → memories never written, cursor advanced** —
  permanent silent drop of the chunk. Worst-case.
- **(b) Cursor last → crash after memory write → next run re-extracts,
  re-spends OpenRouter tokens, re-creates duplicates** — duplicates are
  correct (Orchestrator dedupes), wasted spend is acceptable cost of
  recovery.
- **(c) Interleaved with no atomicity → both** — possible if sidecar write
  is `fp.write()` directly (partial write on crash).

The sidecar sanity check from ADR-007 (`cursor > file_size → reset to 0`)
also interacts with externally-rotated session JSONL files
(halliday Finding #10).

## Options considered
- **Strict ordering memories-then-cursor, atomic sidecar write via
  temp-file + rename, no cursor advance on exception** (chosen) — the
  failure mode is duplicate-on-retry, which is recoverable.
- Cursor-first ordering — rejected: silent drop is unrecoverable.
- Interleaved without atomicity — rejected: produces both failure modes.
- Single-transaction across all four steps (would require the store and
  the sidecar to share a transaction boundary) — out of v1 scope; the
  sidecar is a separate file, not a store table per ADR-harness-004.

## Decision
**Per Daydream invocation:**

1. Acquire the per-session lock
   ([`ADR-dreaming-014`](ADR-dreaming-014-concurrent-daydream-flock.md)).
2. Read sidecar cursor.
3. Read JSONL slice from `cursor` to current EOF; record `new_cursor =
   current_eof`.
4. `redacted = redact(text)`.
5. `completion = client.complete(redacted, ...)`. If
   `completion.text` is empty (per
   [`ADR-dreaming-012`](ADR-dreaming-012-openrouter-missing-key-failopen.md)),
   **abort here without advancing the cursor.**
6. Parse `completion.text` → `list[MemoryItem]`. Any parse error: log,
   emit event, **abort here without advancing the cursor.**
7. For each `MemoryItem`: `orchestrator.write(item)`. The Orchestrator's
   dedup-on-write handles re-writes idempotently.
8. **Only on successful completion of step 7:** write the new cursor
   atomically:
   ```python
   def _write_cursor_atomic(path: Path, new_state: dict) -> None:
       tmp = path.with_suffix(path.suffix + ".tmp")
       tmp.write_text(json.dumps(new_state), encoding="utf-8")
       tmp.replace(path)  # POSIX atomic same-fs rename
   ```
9. Release the lock.

**Any exception in steps 2-7** propagates to a wrapper that logs +
emits an event + returns without advancing the cursor. The next Stop
fire reprocesses the same slice.

**Cursor sanity check** (per ADR-007) is augmented per halliday
Finding #10: if `sidecar.cursor > current_file_size`, the file was
truncated or rotated — reset cursor to 0 and reprocess (duplicate-on-
rotation is the same recoverable failure mode as duplicate-on-retry).

## Rationale
Memories-then-cursor inverts the worst-case from "silent drop" to
"duplicate write, dedup absorbs it." Atomic sidecar via temp + rename
prevents a half-written cursor file from corrupting state. The "abort
without advance" rule on empty completions ties this ADR to
[`ADR-dreaming-012`](ADR-dreaming-012-openrouter-missing-key-failopen.md):
together they guarantee Daydream is *eventually consistent* across
provider-unavailable windows.

## Tradeoffs & risks
- **Duplicate writes on partial failure** — Orchestrator dedup is the
  safety net. If dedup is poorly tuned (open in
  [`ADR-storage-001`](ADR-storage-001-orchestrator-in-process-library.md)),
  we get duplicate memories; correct behavior is delegated, not avoided.
- **Wasted OpenRouter tokens on retry** — accepted cost. Cheaper than
  silent drop.
- **Atomic rename requires same-filesystem temp file** — standard POSIX
  assumption; on Windows behavior is similar but with caveats. Tested
  on macOS/Linux for v1.
- **Cursor sanity reset on rotation = reprocess whole new file** — at a
  cost spike. Halliday Finding #10 follow-up (fingerprint-based rotation
  detection) is queued separately.
- **No atomic guarantee across memory writes and cursor write** — if a
  crash hits between step 7's last memory.write and step 8, we'll
  reprocess and dedup. Acceptable.

## Consequences for the build
- **Contract — source of truth:** the `daydream(*, session_id, log_path,
  store)` entrypoint and the `_write_cursor_atomic` helper in the
  dreaming package.
- **Shape:** wrapper function pattern (steps 1-9 above), with a single
  `try` around steps 2-7 and the cursor write in step 8 reachable only
  on no-exception.
- **Policy — sidecar write is atomic** (`tmp.replace(target)`); never
  open the sidecar file in `"w"` mode directly.
- **Policy — cursor advance is the LAST persistent operation** of a
  successful invocation. Any prior failure aborts without advance.
- **Policy — empty completion text** (per ADR-012) = no advance, same
  as exception.
- **Policy — sanity check on rotation** (`cursor > file_size → reset
  to 0`) emits a `cursor_reset` event so the reprocess is visible.
- **Exhaustive consumers:** the Daydream entrypoint; night Dream
  (PR3+) reads the store, doesn't use the cursor, doesn't apply.

## Open items (dreaming-owned)
- **Rotation fingerprinting** — halliday Finding #10 follow-up: store
  `first_64_bytes_hash` in the sidecar; detect rotation precisely
  (different file content vs truncation) rather than the blunt
  `cursor > file_size` check. Future ADR.
- **Single-transaction story** — if memories and sidecar ever live in
  the same store, revisit the ordering policy (could simplify).
