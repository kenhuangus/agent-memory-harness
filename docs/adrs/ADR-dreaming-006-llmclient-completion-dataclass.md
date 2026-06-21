---
id: ADR-dreaming-006
domain: dreaming
title: LLMClient.complete() returns a Completion dataclass with token counts (replaces ADR-dreaming-003's signature)
status: Accepted
date: 2026-06-21
contract: true
supersedes: ADR-dreaming-003
superseded_by: none
owner: Scott B. (P4) — engine; Keith (P1) — client interface
origin: design session 2026-06-21 (Daydream PR1 gap pass — contradiction between ADR-003 and ADR-004 surfaced during planning)
---

# ADR-dreaming-006: `LLMClient.complete()` returns a `Completion` dataclass with token counts

**Status:** Accepted · **Date:** 2026-06-21 · **Contract:** yes
**Supersedes:** [`ADR-dreaming-003`](ADR-dreaming-003-consolidation-llmclient.md) · **Superseded by:** none

> **Scope of supersession.** This ADR carries forward all of ADR-dreaming-003's
> decisions intact (swappable `LLMClient` interface, OpenRouter-first provider,
> impl roster of `OpenRouterClient` / `LocalClient` / `AnthropicClient`, lazy
> import of heavy deps). It **replaces only ADR-dreaming-003's method
> signature**, which was insufficient to satisfy ADR-dreaming-004's cost-tracking
> requirement.

## Context
[`ADR-dreaming-003`](ADR-dreaming-003-consolidation-llmclient.md) committed
the subconscious to a swappable `LLMClient` interface with signature
`complete(prompt: str, *, system: str|None, max_tokens: int) -> str`.
[`ADR-dreaming-004`](ADR-dreaming-004-default-subconscious-model.md) then
committed that Daydream and Dreaming OpenRouter spend wires into
`eval/memeval/cost.py` so token cost is visible in the same accounting as
agent inference (relevant to `prd.md` §4 `<~10%` memory-token overhead).

`cost.py` provides `cost_of(model, tokens_in, tokens_out)` — it requires
per-call token counts. The existing `ModelAdapter.generate()` (in
[`protocols.py`](../../eval/memeval/protocols.py)) returns
`(text, tokens_in, tokens_out)` for exactly this reason: per its docstring,
*"the harness can feed the CostTracker without a second tokenizer pass."*
The plugin's `ClaudeResult` (in
[`claudecode/cli.py`](../../eval/memeval/claudecode/cli.py)) follows the same
pattern with a dataclass: `text, tokens_in, tokens_out, cost_usd, num_turns, raw`.

ADR-003's `-> str` signature gives the caller no way to know the token counts
the cost tracker needs. The contradiction is unworkable without changing one
of the two ADRs; the cheaper change is the signature.

## Options considered
- **Dataclass `Completion(text, tokens_in, tokens_out)`** (chosen) — mirrors
  the established `ClaudeResult` precedent; named fields are self-documenting;
  future-extensible (e.g. add `cost_usd`, `finish_reason`, `model_used`)
  without breaking existing call sites.
- Tuple `(text, tokens_in, tokens_out)` matching `ModelAdapter.generate()`
  exactly — minimal, but any future field addition is a breaking signature
  change (every call-site unpack updates). Worse for an interface still
  maturing.
- Keep `-> str`; expose token counts via a side channel
  (`client.last_usage`) — anti-pattern (hidden state between calls, race
  conditions in any concurrent use). Rejected.
- Richer dataclass with `cost_usd`, `raw`, `provider` matching `ClaudeResult`
  fully — most informative for `cost.py` wiring, but couples `LLMClient` to
  provider-specific concepts (`raw` shape varies by provider; `cost_usd` may
  not be returned by every backend). The caller already knows the model id
  (it set `DREAM_MODEL`), so it can compute `cost_usd` itself via
  `cost_of(model, c.tokens_in, c.tokens_out)`. Rejected as premature.

## Decision
The shared `LLMClient` interface defined in
[`ADR-dreaming-003`](ADR-dreaming-003-consolidation-llmclient.md) takes this
signature instead:

```python
@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    tokens_in: int
    tokens_out: int

class LLMClient(Protocol):
    def complete(
        self, prompt: str, *, system: str | None = None, max_tokens: int = ...
    ) -> Completion: ...
```

All other decisions in ADR-dreaming-003 stand: provider is OpenRouter by
default (`DREAM_PROVIDER=openrouter`), default model is per
[`ADR-dreaming-004`](ADR-dreaming-004-default-subconscious-model.md), the
client is lazy-imported, alternate impls are `LocalClient` and
`AnthropicClient`.

## Rationale
A signature that returns text but hides the token counts the cost ledger
requires forces every caller into a workaround (separate tokenizer call,
hidden state on the client, parsing back from logs). Returning a small
dataclass costs nothing at the call site (`c = client.complete(...);
c.text; c.tokens_in`) and removes the contradiction with ADR-004 by
construction. Choosing dataclass over tuple is the same call the existing
`ClaudeResult` made — keeps Daydream's interface aligned with what the rest
of the codebase already does.

## Tradeoffs & risks
- **Marginally more code at the call site than `-> str`.** `c.text` vs the
  prior `text` — a one-character cost.
- **Token-count reliability varies by provider.** OpenRouter typically
  returns `usage` in the response; `EchoClient` has no real call so its
  counts are synthetic. Decided in Open items below.
- **Migration cost for any code that already built against ADR-003's
  signature.** Currently zero: no `LLMClient` consumer exists yet in tree.
  This ADR lands before the implementation does, so there are no call-site
  updates to make.
- **Cross-owner change.** Keith co-owns ADR-003's client interface; this
  ADR is filed in Scott's dreaming domain but Keith's review is expected
  on the PR.

## Consequences for the build
- **Contract — source of truth:** the `LLMClient` Protocol + `Completion`
  dataclass in the dreaming package's LLM module (e.g.
  `eval/memeval/dreaming/llm.py`).
- **Shape:**
  ```python
  @dataclass(frozen=True, slots=True)
  class Completion:
      text: str
      tokens_in: int
      tokens_out: int

  class LLMClient(Protocol):
      def complete(
          self, prompt: str, *, system: str | None = None,
          max_tokens: int = ...,
      ) -> Completion: ...
  ```
  Impls: `OpenRouterClient` (default), `LocalClient`, `AnthropicClient`,
  plus `EchoClient` for tests / offline path.
- **Policy — cost.py wiring:** every `complete()` call site is responsible
  for piping the returned `tokens_in`/`tokens_out` into
  `cost_of(client.model, ...)` so Daydream and Dreaming spend register in
  the same accounting as agent inference (per ADR-dreaming-004).
- **Policy — `OpenRouterClient`** extracts token counts from the response's
  `usage` field (OpenAI-compatible shape). Missing `usage` → log + return
  `tokens_in=0, tokens_out=0` (fail-open per
  [`ADR-harness-006`](ADR-harness-006-fail-open.md)). Cost tracker will show
  the call as zero-cost, which is observable.
- **Policy — `EchoClient`** returns deterministic synthetic counts
  (e.g. `tokens_in = len(prompt)//4`, `tokens_out = len(text)//4`). Tests
  can assert on exact values.
- **Exhaustive consumers** of the `Completion` shape: Daydream chunk
  extraction (per [`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md)),
  Dreaming whole-store consolidation (per
  [`ADR-dreaming-002`](ADR-dreaming-002-dreaming-consolidation-cli.md)), and
  any future caller of the shared subconscious client.

## Open items (dreaming-owned)
- **`EchoClient` token-count formula** — decide a deterministic estimator
  (char-count / 4 is the rough OpenAI heuristic) and pin it in tests.
- **Local provider token counts** — `LocalClient` (Ollama) returns
  `eval_count` / `prompt_eval_count` in its responses; map those to
  `tokens_in` / `tokens_out` when implementing.
- **`AnthropicClient`** — Anthropic's `usage` field uses `input_tokens` /
  `output_tokens`; trivial mapping when implementing.
