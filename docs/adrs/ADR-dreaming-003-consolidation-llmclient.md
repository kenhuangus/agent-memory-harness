---
id: ADR-dreaming-003
domain: dreaming
title: Subconscious model — swappable LLMClient, OpenRouter-first
status: Accepted
date: 2026-06-19
contract: true
supersedes: none
superseded_by: none
owner: Scott B. (P4) — engine; Keith (P1) — client interface
origin: docs/harnesses/05-plugin-mvp-plan.md (ADR-P6)
---

# ADR-dreaming-003: Subconscious model — swappable `LLMClient`, OpenRouter-first

**Status:** Accepted · **Date:** 2026-06-19 · **Contract:** yes
**Supersedes:** none · **Superseded by:** none

## Context
The board labels the subconscious's model "Not frontier"; the meeting left "local vs
cheap OpenRouter" open. The model's task is extraction/classification ("what in these
logs is worth remembering?") and consolidation, not frontier reasoning. The engine is
stdlib-only at import — any model client must be lazy-imported.

This is the **one model client shared by both subconscious functions** —
Daydreaming ([`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md)) and
Dreaming ([`ADR-dreaming-002`](ADR-dreaming-002-dreaming-consolidation-cli.md)). It
is a *shared helper*, not a shared entrypoint: the two functions stay isolated
(separate callables / triggers) but both reach for the same `LLMClient` interface.

## Options considered
- **Swappable `LLMClient` interface, OpenRouter-first, cheap default model:** one
  OpenRouter key reaches many cheap models; Anthropic and local are alternate impls.
- Hosted Anthropic (Haiku) default — coherent with the thesis but ties the
  subconscious to one provider.
- Local default (Ollama) — free/private but a heavy install prerequisite.

## Decision
**A swappable `LLMClient` interface with OpenRouter as the starting provider and a
cheap model as the default;** local and Anthropic impls drop in behind the same
interface.

## Rationale
OpenRouter gives the widest cheap-model menu behind one key, which suits a
"let benchmarking pick the model" posture — the open question becomes an empirical
*swap*, not a blocker for the Monday dry run. The interface keeps the engine's
"lazy-import heavy deps" rule and lets a privacy-sensitive user point at a local
model for free (relevant to the redaction trust boundary in
[`ADR-harness-005`](ADR-harness-005-log-adapter-redaction.md)).

## Tradeoffs & risks
API cost and latency per invocation; a network dependency; an OpenRouter key required
for the default path (acceptable for a research artifact). The default model id is
config so we can change it without a code change.

## Consequences for the build

- **Contract — source of truth:** the `LLMClient` interface in the memory package.
- **Shape:** `LLMClient.complete(prompt: str, *, system: str|None, max_tokens:int)
  -> str`; impls: `OpenRouterClient` (default), `LocalClient`, `AnthropicClient`.
  Selected by `DREAM_MODEL` / `DREAM_PROVIDER` env.
- **Exhaustive consumers:** both subconscious functions — Daydreaming
  ([`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md)) and Dreaming
  ([`ADR-dreaming-002`](ADR-dreaming-002-dreaming-consolidation-cli.md)) — use the
  same `LLMClient` interface. It is a shared *helper*, not a shared entrypoint.
