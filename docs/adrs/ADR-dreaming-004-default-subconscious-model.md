---
id: ADR-dreaming-004
domain: dreaming
title: Default subconscious model — inclusionai/ling-2.6-flash via OpenRouter
status: Accepted
date: 2026-06-20
contract: false
supersedes: none
superseded_by: none
owner: Scott B. (P4)
origin: design session 2026-06-20 (Daydream PR1 planning)
---

# ADR-dreaming-004: Default subconscious model — `inclusionai/ling-2.6-flash` via OpenRouter

**Status:** Accepted · **Date:** 2026-06-20 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
[`ADR-dreaming-003`](ADR-dreaming-003-consolidation-llmclient.md) establishes the
swappable `LLMClient` interface with OpenRouter as the starting provider, and
states *"the default model id is config so we can change it without a code
change"* — leaving the specific default value open. That value is a load-bearing
choice: it sets the cost, latency, and quality floor of the entire subconscious
(both Daydreaming and Dreaming share the client per ADR-dreaming-003).

The subconscious does extraction/classification on session logs, not frontier
reasoning. Long-term it must be **capable** of: structured JSON output (clean
`MemoryItem` extraction), instruction following (extract specific item types and
respect schema), long context (whole-turn or multi-turn chunks per
[`ADR-harness-003`](ADR-harness-003-log-extraction-chunking.md)), and code
awareness (sessions are coding-agent traces). Cheaper preferred. Faster preferred.
"Not frontier" per `architecture.md` §1.

## Options considered
All verified against OpenRouter `/api/v1/models` on 2026-06-20:
- **`inclusionai/ling-2.6-flash`** (chosen) — 262k context, $0.01 / $0.03 per M
  in/out, strict `structured_outputs` + `tools` + `response_format`, "instant"
  tier (fastest).
- `cohere/north-mini-code:free` — true $0, 256k context, code-tuned, but
  subject to OpenRouter account-level free-tier rate caps and feature support
  unverified for our extraction shape.
- `deepseek/deepseek-v4-flash` — 1M context, $0.09 / $0.18 per M, strict
  structured outputs, strong code/reasoning reputation. Trade-up target.
- `xiaomi/mimo-v2.5` — 1M context, $0.14 / $0.28 per M, full feature set.
- `deepseek/deepseek-v4-pro` — 1M context, $0.435 / $0.87 per M, quality
  ceiling among "cheap." Reserve for if v1 eval shows the lighter models
  extract poorly.

## Decision
The default value of `DREAM_MODEL` is **`inclusionai/ling-2.6-flash`** when
`DREAM_PROVIDER=openrouter` (the default provider per
[`ADR-dreaming-003`](ADR-dreaming-003-consolidation-llmclient.md)). Trading up to
any of the alternatives above is a one-env-var change.

## Rationale
Lowest cost among the models that meet the full capability set
(structured outputs + tools + ≥256k context). At ~$0.05 per 100 sessions of 50k
tokens, the model is effectively free at our eval scale without the account-level
rate caps that bite "true free" tiers. The "instant" tier minimizes the
session-end latency a `Stop`-fired Daydream pass adds. Picking a model with strict
structured-output support means JSON-shape extraction is enforced at the API layer
rather than in fragile prompt engineering.

## Tradeoffs & risks
- **Quality not yet benchmarked for our extraction shape.** First eval may show
  it underperforms `deepseek-v4-flash` on coding-agent traces; the trade-up is
  one env-var change, no code change.
- **Free-model-tier churn.** OpenRouter rotates free/cheap tiers (verified
  list on 2026-06-20 may not hold in 60 days). If `ling-2.6-flash` exits or its
  pricing changes, we swap via env var; no migration cost.
- **Provider concentration.** Provider is Inclusion AI; accepted per the
  "no provider/data-routing constraint" call in the same design session. If a
  downstream user has a stricter posture, they override via `DREAM_MODEL` /
  `DREAM_PROVIDER`.
- **Cost still has to be counted.** A "$0.05" estimate hides if Daydream loops
  or chunking explodes. Cost tracking wires into `eval/memeval/cost.py` so
  spend is visible in the same accounting as agent inference (relevant to
  `prd.md` §4 < ~10% memory-token overhead target).

## Consequences for the build
- **Policy:** `OpenRouterClient` (per
  [`ADR-dreaming-003`](ADR-dreaming-003-consolidation-llmclient.md)) uses
  `inclusionai/ling-2.6-flash` when `DREAM_MODEL` env var is unset.
- **Policy:** Daydream and Dreaming OpenRouter spend is tracked in
  `eval/memeval/cost.py` alongside agent-inference costs.
- **Policy:** revisit this ADR (write a successor, do not edit) when the first
  eval pass produces extraction-quality numbers, or if the model exits OpenRouter.

## Open items (dreaming-owned)
- **Quality benchmark.** A small evaluation harness comparing extraction
  precision/recall on a synthetic session log across the five candidates, used
  to validate or revise this default after v1 lands.
