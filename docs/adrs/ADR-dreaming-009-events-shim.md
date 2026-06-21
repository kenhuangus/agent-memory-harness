---
id: ADR-dreaming-009
domain: dreaming
title: Daydream events shim — no-op + local daydream-events.jsonl diary until harness-007 ships
status: Accepted
date: 2026-06-21
contract: false
supersedes: none
superseded_by: none
owner: Scott B. (P4)
origin: design session 2026-06-21 (Daydream PR1 gap pass)
---

# ADR-dreaming-009: Daydream events shim — no-op + local `daydream-events.jsonl` diary until harness-007 ships

**Status:** Accepted · **Date:** 2026-06-21 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

> **Scope.** This ADR covers a **Daydream-only**, **dreaming-domain** interim
> emitter. It is **not** an implementation of
> [`ADR-harness-007`](ADR-harness-007-memory-events-stream.md), which
> remains Keith's system-wide events stream → Langfuse. When that lands,
> this shim's implementation swaps; the call sites do not.

## Context
[`ADR-harness-007`](ADR-harness-007-memory-events-stream.md) commits to a
structured memory-events stream bound to an observability platform
(Langfuse). It is not implemented yet.
[`ADR-dreaming-005`](ADR-dreaming-005-v1-inline-redaction.md) commits
Daydream to emit redaction events through the events stream — but Daydream
cannot block on Keith's implementation without re-introducing the
sequential cross-domain dependency the three-iteration ramp avoids
([`ADR-harness-006`](ADR-harness-006-fail-open.md) rationale).

A pure no-op shim would lose information that's useful during v1 bring-up
(false-positive rate per chunk, redaction counts, cost-per-chunk, cursor
advances). A local diary file preserves it without competing with Keith's
stream.

## Options considered
- **No-op shim with eventual API surface + local Daydream-scoped diary
  file** (chosen) — preserves info during the gap, throwaway by design,
  one-line impl swap when harness-007 ships.
- Pure no-op shim — loses observability during the gap; Daydream debug
  worse than necessary.
- Block on harness-007 — sequential cross-domain dependency.
- Build a full events stream in dreaming domain — crosses into harness
  territory, risks calcifying as a competing implementation.

## Decision
Ship an `emit()` function in `eval/memeval/dreaming/events.py` with the
shape Keith's eventual API is expected to take:

```python
def emit(event_type: str, **fields: Any) -> None: ...
```

During the gap, the implementation:
1. Appends a JSON line `{ "ts": ..., "event_type": ..., **fields }` to
   the file `${MEMORY_STORE%/*}/dream/<session_id>.daydream-events.jsonl`.
2. Does **not** call into any system-wide events stream (it doesn't
   exist yet).
3. Is fail-open: a write error logs and returns, never raises.

The diary file:
- Is **named `daydream-events.jsonl`** (not `memory-events.jsonl`) to
  make its scope unmistakable.
- Lives in the dreaming-namespaced sidecar directory (same dir as
  [`ADR-harness-004`](ADR-harness-004-dream-state-sidecar.md)'s sidecar
  state), keyed by session id.
- Is gitignored.
- Captures only events Daydream emits — redaction counts per chunk,
  LLM call timing/tokens, extracted-memory counts, cursor advances,
  sanity-check resets.

When [`ADR-harness-007`](ADR-harness-007-memory-events-stream.md)'s stream
lands, a successor ADR records the migration: `emit()` swaps to call into
Keith's stream; the diary write either stops or remains as a debug mirror
(decided then, not now). Call sites do not change.

## Rationale
The cost is small (one function, one append-per-call), the API matches the
eventual contract (zero call-site churn on migration), and the diary
preserves the observability the FP-rate measurement in
[`ADR-dreaming-005`](ADR-dreaming-005-v1-inline-redaction.md)'s open items
needs. Naming the file `daydream-events.jsonl` keeps the scope visible at
a glance — it would never be mistaken for the system-wide stream.

## Tradeoffs & risks
- **Risk: shim calcifies as a competing implementation.** Mitigated by
  explicit scope (Daydream-only), explicit naming (`daydream-events`),
  and a tracked migration to harness-007.
- **One more file per session** to manage; appended unbounded for the
  session. Bounded in practice by single-Daydream-pass writes (few events
  per pass).
- **Lost during the gap if a session dies before harness-007 ships** —
  acceptable; nothing is consuming events yet.
- **Time-of-event** uses wall clock; not monotonic. Sufficient for
  debugging; not load-bearing.

## Consequences for the build
- **Policy — file location:**
  `${MEMORY_STORE%/*}/dream/<session_id>.daydream-events.jsonl`. Same dir
  as the sidecar state; same per-session keying.
- **Policy — naming:** the file is `daydream-events.jsonl`. Not
  `memory-events.jsonl`. Not `events.jsonl`.
- **Policy — scope:** only Daydream-domain code (`eval/memeval/dreaming/`)
  imports and calls `emit()`. No other module writes to the diary file.
- **Policy — gitignore:** `*.daydream-events.jsonl` pattern added to the
  repo's `.gitignore` to prevent accidental commits.
- **Policy — fail-open:** `emit()` never raises. A failed diary write
  logs (via stdlib `logging`) and returns.
- **Policy — migration trigger:** when
  [`ADR-harness-007`](ADR-harness-007-memory-events-stream.md) is
  implemented and importable, a successor ADR (Scott authors, Keith
  co-signs) swaps `emit()`'s body. Call sites stay untouched.

## Open items (dreaming-owned)
- **Diary retention** — bounded growth strategy if a session generates
  many events (e.g. rotate at N MB). Not load-bearing for v1; add when
  needed.
- **Event-schema versioning** — when harness-007 ships, the diary's
  past entries may be in a schema that's diverged. Migration may need a
  one-off translator or just discard the diary.
- **Decide diary's fate at migration time:** stops being written, OR
  stays as a local-debug mirror. Decision deferred until harness-007
  has a concrete shape.
