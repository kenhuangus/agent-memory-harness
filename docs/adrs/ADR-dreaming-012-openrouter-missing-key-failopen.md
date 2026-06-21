---
id: ADR-dreaming-012
domain: dreaming
title: OpenRouterClient missing-API-key behavior — no-op Completion + event + no cursor advance
status: Accepted
date: 2026-06-21
contract: true
supersedes: none
superseded_by: none
owner: Scott B. (P4) — engine; Keith (P1) — informed (LLMClient consumer)
origin: design session 2026-06-21 (halliday adversarial-review Finding #2)
---

# ADR-dreaming-012: `OpenRouterClient` missing-API-key behavior — no-op `Completion` + event + no cursor advance

**Status:** Accepted · **Date:** 2026-06-21 · **Contract:** yes
**Supersedes:** none · **Superseded by:** none

## Context
[`ADR-dreaming-006`](ADR-dreaming-006-llmclient-completion-dataclass.md) gives
`LLMClient.complete()` the signature returning `Completion(text, tokens_in,
tokens_out)` and specifies: *"Missing `usage` → log + return `tokens_in=0,
tokens_out=0` (fail-open)."* It does **not** specify what happens when the
key itself is unset.

Halliday's 2026-06-21 review (Finding #2, HIGH) flagged the gap: tracing
the Stop-hook path, if `OpenRouterClient.__init__` or `.complete()` raises
when `OPENROUTER_API_KEY` is unset, the chunk loop is mid-flow — sidecar
cursor state and partial writes are at risk. ADR-harness-006 says hooks
fail-open, but that puts the burden on the wrapper to translate a hard
`RuntimeError("key unset")` into a no-op — fragile.

Most load-bearing observation: *"Cursor non-advance is the load-bearing
part — without it, the next run silently skips that turn."*

## Options considered
- **No-op `Completion('', 0, 0)` + `llm_unavailable` event + caller checks
  text before advancing cursor** (chosen) — honors fail-open at the
  LLMClient layer; failure is visible via events; retry-when-key-set is
  free (cursor never moved).
- Hard-raise at `__init__` or `.complete()` — violates ADR-006/harness-006
  fail-open spirit; pushes failure handling into every caller.
- Silent empty-Completion with no event — invisible failure mode; failures
  accumulate without operator awareness.
- Check `OPENROUTER_API_KEY` upstream in Daydream before constructing the
  client and skip if unset — couples Daydream to provider-specific env
  vars; doesn't generalize to other providers.

## Decision
**Behavior when `DREAM_PROVIDER=openrouter` and `OPENROUTER_API_KEY` is
unset (or empty):**

1. `OpenRouterClient.__init__` succeeds; the missing-key state is held on
   the instance.
2. `OpenRouterClient.complete()` does NOT call the network; returns
   `Completion(text="", tokens_in=0, tokens_out=0)` and emits
   `emit("llm_unavailable", provider="openrouter", reason="OPENROUTER_API_KEY unset")`
   via the [`ADR-dreaming-009`](ADR-dreaming-009-events-shim.md) shim.
3. The Daydream chunk loop MUST check `completion.text` — on empty, skip
   memory extraction for that chunk AND **do NOT advance the sidecar
   cursor**
   ([`ADR-dreaming-013`](ADR-dreaming-013-cursor-advance-ordering.md)).
4. The next Daydream invocation (after the user sets the key) reprocesses
   the same chunk from the unchanged cursor.

The same pattern applies to `LocalClient` (Ollama unavailable) and
`AnthropicClient` (`ANTHROPIC_API_KEY` unset) when their full impls land.

## Rationale
Fail-open at the LLMClient layer keeps Daydream's contract with
ADR-harness-006 intact: the agent's session never breaks because of a
missing key. Cursor non-advance is the second half of the contract:
without it, "fail-open" silently loses the unprocessed turn. The
events-stream tripwire makes the rare "no-op call" visible to the
operator, who can re-run after setting the key with no data loss.

## Tradeoffs & risks
- **Reprocessing on retry** is correct behavior, not a defect — redaction
  is idempotent, Orchestrator dedup handles re-writes per
  [`ADR-storage-001`](ADR-storage-001-orchestrator-in-process-library.md).
- **Long unset-key periods** generate one `llm_unavailable` event per
  Stop hook; the events stream catches the cadence. Daydream behaves as
  no-op until the key returns; cursor stays put.
- **Detecting "key set but invalid"** (vs unset) is a separate concern —
  401/403 from the real call goes through ADR-006's fail-open
  (`tokens_in=0, tokens_out=0`); cursor SHOULD still not advance on a
  401/403, which couples this ADR with ADR-013.
- **EchoClient** and tests are unaffected — they have no API key concept.

## Consequences for the build
- **Contract — source of truth:** `OpenRouterClient` class in
  `eval/memeval/dreaming/llm.py` + the Daydream chunk-loop wrapper.
- **Shape:**
  ```python
  class OpenRouterClient:
      def __init__(self, model: str, *, api_key: str | None = None): ...

      def complete(
          self, prompt: RedactedText, *,
          system: RedactedText | None = None, max_tokens: int = ...,
      ) -> Completion:
          if not self._api_key:
              emit("llm_unavailable", provider="openrouter",
                   reason="OPENROUTER_API_KEY unset")
              return Completion(text="", tokens_in=0, tokens_out=0)
          # ... real network call ...
  ```
- **Policy — caller (Daydream chunk loop):** must check
  `completion.text`; empty → skip extraction, skip cursor advance, log
  `chunk_skipped_unavailable_llm` event.
- **Policy — generalize:** when `LocalClient` / `AnthropicClient` ship,
  apply the same pattern (empty `Completion` + `llm_unavailable` event).
- **Policy — distinguishing missing-key from network errors:** missing
  key is a *configuration* failure, network 401/403 is a *runtime*
  failure; both result in empty Completion + no cursor advance, but
  emit different event subtypes (`llm_unavailable` vs `llm_call_failed`)
  so operators can triage.
- **Exhaustive consumers:** Daydream chunk-extraction loop (the only
  v1 consumer); night Dream (PR3+, same pattern).

## Open items (dreaming-owned)
- **Sibling client behavior:** when `LocalClient` / `AnthropicClient`
  ship, write a brief successor confirming they follow the same pattern
  (no separate ADR needed; reference this one).
- **Event subtype taxonomy:** decide the full set of
  `llm_*` event subtypes (`llm_unavailable`, `llm_call_failed`,
  `llm_rate_limited`, `llm_malformed_response`) when PR2 lands.
