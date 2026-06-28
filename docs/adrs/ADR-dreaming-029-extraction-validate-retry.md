---
id: ADR-dreaming-029
domain: dreaming
title: One-shot validate-retry on unparseable extraction output — rescue a paid call, default on
status: Accepted
date: 2026-06-28
contract: false
supersedes: none
superseded_by: none
owner: cookbook-improvement-loop
origin: suggestion1.md idea 5 (MRAgent review); cookbook-improvement-loop Tier-2 gate
---

# ADR-dreaming-029: One-shot validate-retry on unparseable extraction output

**Status:** Accepted · **Date:** 2026-06-28 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context

`extract_memories` makes one paid LLM call per chunk, then parses the JSON. `_loads_lenient`
already recovers the common deviations (markdown fences, prose wrapping, a `{...}` span inside
chatter). The residual failure mode is **genuinely invalid or truncated** JSON — e.g. a
`max_tokens` cutoff mid-object — where the chunk is dropped to `None` (`chunk_skipped_parse_failed`)
and a paid extraction is lost. MRAgent's robustness idea (`suggestion1.md` idea 5) is to re-ask the
model for valid output before giving up.

## Decision

On a parse/shape failure (`_parse_validate` returns `None`), fire `daydream.extract_retry` and make
**one** corrective re-prompt — the same envelope + `max_tokens`, with `_RETRY_SUFFIX` appended to the
system prompt ("your previous response could not be parsed; return only a JSON object with keys
`memories` and `rejected`"). Re-validate; only then drop the chunk. Bounded to a single retry, and it
fires **only on the failure path** — a successful first parse never pays for it.

- `$DREAM_EXTRACT_RETRY` — `1`/on (default) or `0`/off to restore the pre-029 drop-on-failure behavior.
- The parse + top-level shape check is extracted into the pure `_parse_validate(text)` helper (no
  emits, no LLM) so the retry decision is made before any outcome is recorded.

## Evidence

This is a robustness change; its benefit is on **malformed** output, which the valid-JSON fixture
transcripts never produce — so the Tier-2 yield A/B is **neutral by construction** (retry never
triggers; yield identical, never worse). The gain is proven on the failure path it targets by a
deterministic unit test (`test_extract_retry.py`): a stub returning invalid-then-valid JSON recovers
a memory the pre-029 code dropped (`yield 0 → 1`), `DREAM_EXTRACT_RETRY=0` reproduces the old drop,
and a valid first response spends no retry. Full `test_extract.py` suite: 175 passed (the one
remaining failure, `test_extract_resists_injection_payload`, fails identically on `origin/main` —
pre-existing, unrelated to this change).

## Consequences

- Strictly ≥ baseline yield: recovers droppable chunks, never discards a parseable one. Worst case is
  one extra LLM call on a chunk that was already going to cost a re-extraction or be lost.
- Architecture guards updated for the new `os` import (env toggle) and the `daydream.extract_retry`
  event name (now 10 allowed events).
- Not Tier-3-confirmed. Tier-1/2 are necessary, not sufficient.
