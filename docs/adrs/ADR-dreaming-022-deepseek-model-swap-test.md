# ADR-dreaming-022 — test deepseek-v4-flash as the daydream model

- **Status:** Accepted
- **Date:** 2026-06-23
- **Owner:** Scott (dreaming)
- **Contract:** false
- **Supersedes:** none (ADR-dreaming-004 remains the default-model decision)
- **Superseded by:** none

## Context

The daydream extractor currently uses `inclusionai/ling-2.6-flash` per
ADR-dreaming-004. That choice was made early in the sprint based on (a) free-tier
OpenRouter availability and (b) latency-budget compatibility with the Stop-hook
8-second-default drain. We have no comparative data on any alternative model's
extraction quality, latency, parse-failure rate, or substrate-shape distribution
on the actual workloads the harness sees.

Two parallel motivations push for testing an alternative:
1. **Substrate-quality calibration.** The prompt-variant research arc
   (`KB-dreaming.md`, the recent multi-agent workflow) repeatedly hits the
   question "is what we're measuring an artifact of the model or the prompt?"
   Without a second model data point, that question is unanswerable.
2. **End-to-end `.env` wiring verification.** The audit for this ADR found that
   `.env.example` lines 6-9 falsely state "nothing in the tree auto-loads `.env`"
   — in fact every entrypoint calls `memeval.dotenv_loader.load_root_dotenv()`
   (`cli.py:269`, `run_bench.py:251`, `pipeline.py:632`, plugin `cli.py:164`,
   `hooks_handler.py:155`). A model swap is the cheapest end-to-end test of
   this wiring — observable in `events.jsonl` once the new
   `llm_call_succeeded` event lands.

`deepseek/deepseek-v4-flash` is picked as the alternative for cost (cheap on
OpenRouter), declared-target use case (general-purpose extraction-friendly), and
the operator's existing OpenRouter quota covering it.

## Decision

For an opt-in test period, the daydream model is set to
`DREAM_MODEL=deepseek/deepseek-v4-flash` via the local `.env`. The hardcoded
default in `eval/memeval/dreaming/llm.py:39` remains `inclusionai/ling-2.6-flash`
— this ADR does NOT supersede ADR-dreaming-004's default-model decision. It
records a TEST, not a switch.

Adjacent decisions bundled into this ADR's PR (small, in-scope):

1. **Fix the stale `.env.example` "nothing auto-loads .env" docstring.** Replace
   with accurate description of the `load_root_dotenv()` wiring + the
   `override=False` semantics + the `MEMEVAL_DOTENV` override path.
2. **Add an `llm_call_succeeded` event to `OpenRouterClient.complete()`.** All
   five FAILURE paths already emit `model=self.model`; the SUCCESS path was the
   only one without observability of which model answered. Adding the symmetric
   emit gives the smoke test (and any future production audit) a reliable signal.
3. **Add `test_dream_model_env_override`** as a regression on the env-to-model
   contract; covers DREAM_PROVIDER + DREAM_MODEL + the explicit-arg-wins path.

## Rationale

- **Why deepseek-v4-flash specifically?** Cost + OpenRouter availability + the
  operator's existing quota. Not because of any published comparative
  benchmark — this is an exploratory data point, not a defended choice.
- **Why test via `.env` rather than swap `DEFAULT_MODEL`?** Swapping
  `DEFAULT_MODEL` would change behavior for every operator including those who
  haven't opted in, and would invalidate the existing single SWE-Bench-CL run on
  disk as a baseline. Opt-in via `.env` keeps the default stable.
- **Why an ADR at all if this is just a test?** The `.env.example` docstring fix
  is a contract-adjacent change (operators reading the file may have been
  building mental models on the wrong premise). The `llm_call_succeeded`
  observability addition changes the event stream and downstream consumers
  (events.jsonl readers) may want to know. Recording both inline with the test
  rationale keeps the WHY auditable.

## Tradeoffs and risks

- **Adds event volume.** `llm_call_succeeded` fires on every successful daydream
  LLM call (one per chunk per session). At ~50-200 candidates/task on
  SWE-Bench-CL, this could be ~10-30 extra events per task. Acceptable cost;
  events are already gigabytes of JSONL on full runs, and the cardinality is
  bounded.
- **deepseek-v4-flash may not exist on OpenRouter or may have different
  rate-limit characteristics.** If the smoke test (`06-smoke_test.py --call`)
  fails, the model is unreachable and we need to either: (a) update to a slug
  OpenRouter does have, or (b) revert to ling-2.6-flash. Fail-open paths in
  `OpenRouterClient` mean an unreachable model degrades to no-op extraction
  rather than crashing the hook.
- **The .env-overrides-shell-export gotcha.** Operators who have
  `DREAM_*` exported in a shell profile will silently override this `.env`
  value. The smoke test surfaces existing shell env vars to make this
  visible; the ADR records the behavior for future debugging.
- **Promoting deepseek to default would require its own ADR.** This ADR does
  NOT make that switch and should NOT be cited as authority for one. A future
  ADR-dreaming-NNN superseding ADR-dreaming-004 is the path if comparative
  data justifies the change.

## Consequences

- `.env.example` accurately documents `.env` loading (one-time doc-debt fix).
- `events.jsonl` from any run after this PR will carry `llm_call_succeeded`
  events with the model name; existing event consumers should treat this as an
  additive change (no field removed, no schema break).
- The regression test guards env-to-model wiring against future drift.
- A path exists for comparative data between two daydream models without
  changing the default for anyone.

## Open items

- After 1-2 weeks of opt-in deepseek use, compare substrate metrics
  (`daydream.chunk_extracted.n_items` distribution, `daydream.candidate_rejected`
  rate, `chunk_skipped_parse_failed` rate, mean tokens_out per chunk) against
  ling-2.6-flash baseline data. If deepseek wins clearly, draft a successor ADR
  to supersede ADR-dreaming-004.
- Consider whether `llm_call_succeeded` should also be emitted by future
  `LocalClient` / `AnthropicClient` implementations for cross-provider
  observability parity. Out of scope for this ADR.
