# ADR-dreaming-023 — selectable EXTRACTION_SYSTEM_PROMPT variants via env

- **Status:** Accepted
- **Date:** 2026-06-23
- **Owner:** Scott (dreaming)
- **Contract:** false (adds a selector; the prompts themselves are runtime-internal)
- **Supersedes:** none (ADR-dreaming-004 default-model decision unchanged; this is independent)

## Context

The prompt-variants research arc (workflow `wf_21837159-ad4`, substrate-side sweep `bench-run/results.jsonl`) produced four candidate `EXTRACTION_SYSTEM_PROMPT` shapes:

- **V0** — the current MODERATE-threshold prompt with chat-shaped INCLUDE/REJECT examples (the codebase baseline).
- **V1** — STRICT (annoyance-prevention only): emit only when forgetting would visibly annoy the user; drop named decisions, drop implicit commitments, drop opinions.
- **V2** — A-MEM: extend the per-memory schema with `keywords` (3–7 FAISS-friendly terms) and `context` (one-sentence future-relevance) per memory.
- **V3** — SWE-tuned: reframe the opener for an autonomous coding agent; replace the INCLUDE block with code-shaped examples (engineering preferences, project conventions, code-shaped decisions); extend the REJECT block with code-shaped reject anchors (pytest output, diff lines, transient implementation narration).

The substrate-side mini-sweep on real bench redact-audit chunks (27 chunks × 4 variants on `deepseek/deepseek-v4-flash`, 2026-06-23) demonstrated:

- **V3 ranks first** on bench-shaped (SWE-CL) input — kept 8/27, mechanism fires as designed on code-domain transcripts.
- **V2 ranks second** — kept 7/27 with rich non-vapid keywords (`src/_pytest/pastebin.py`, `HTTP 400`) and context strings; parse-fail rate 0% on bench-shaped input (vs 13% on chat-shaped input).
- **V0 ranks third** — kept 3/27. The chat-shaped examples leave signal on the table on code-shaped input.
- **V1 hits zero** on bench data — STRICT's markers (`my name is`, `I prefer`, `remember this`) require human-in-loop signal that an autonomous agent's transcripts don't contain.

The substrate-side sweep does NOT establish bench-side (FWT / task-solve-rate) winners — that's still floor-effect-limited per the earlier bench audit. What it establishes is **prompt-behavior under realistic input**, which is sufficient for opting into a variant in production but not for declaring a default switch.

Without runtime selection, comparing variants on real workloads requires source edits + rebuilds. Operators have no way to test a variant on their own data without churning the repo.

## Decision

Add four named variants of `EXTRACTION_SYSTEM_PROMPT` to `eval/memeval/dreaming/prompts.py` (`EXTRACTION_SYSTEM_PROMPT` = V0 / `EXTRACTION_SYSTEM_PROMPT_V1` / `_V2` / `_V3`), each individually sha256-pinned in `eval/memeval/dreaming/tests/test_prompts.py`. Add a runtime selector `get_extraction_prompt(variant=None)` that resolves the active prompt per call:

1. Explicit `variant` arg wins (used by tests + future programmatic dispatch).
2. `DREAM_EXTRACTION_VARIANT` env var (case-insensitive: `V0`/`V1`/`V2`/`V3`).
3. Default `V0` when both are unset (the existing `EXTRACTION_SYSTEM_PROMPT`).

`_extract.extract_memories` calls `get_extraction_prompt()` once per chunk extraction. Resolution is per-call (not at import time) so operators can swap variants without a process restart — useful for A/B-style testing and for the `DREAM_EXTRACTION_VARIANT` env var to take effect when the bench harness forwards it via `_add_dream_env` (which already globs `DREAM_*`).

Unknown variant names raise `ValueError` naming the legal options — same error shape as `llm.make_client` on unknown `DREAM_PROVIDER`.

The `.env.example` documents the variant by uncommented-or-not pattern: all four `DREAM_EXTRACTION_VARIANT=Vn` lines commented out, with per-variant guidance (the operator uncomments exactly one).

## Rationale

- **Why selectable, not switch-the-default?** The substrate sweep ranks variants on substrate behavior; it does NOT measure bench-side FWT impact (the bench is still in the floor-effect regime per ADR-eval — accuracy=0.0 across stages). Promoting V3 to default would be premature; promoting V2 would silently underwhelm because the recall path doesn't yet read its new fields. Opt-in is the honest middle ground.
- **Why env-var selection, not a CLI flag?** Daydream is invoked from the Stop hook subprocess (`daydream-cli`), not directly by the user. The Stop hook can't accept a CLI flag from the user mid-session, but it does inherit env vars from the parent claude-code process. The bench harness already forwards `DREAM_*` env vars to the plugin subprocess via `_add_dream_env` (`agent.py:281`), so a `DREAM_EXTRACTION_VARIANT` value set in `.env` or shell-exported automatically reaches the daydream call site.
- **Why per-call resolution, not at module-load?** Per-call lets operators flip variants mid-process (useful for unit tests with `monkeypatch.setenv`, and for any future programmatic dispatch). The cost is one dict lookup per chunk — negligible compared to the LLM call.
- **Why backward-compatible default?** `EXTRACTION_SYSTEM_PROMPT` stays exported and unchanged, sha256 pin (`b2f8f69b…`) stays valid in all three pin sites. Operators who don't opt in see zero behavior change.
- **Why sha256-pin all four variants?** The drift-detection contract that protects V0 applies equally to the variants. A reviewer who casually edits V3 should get a red test, same as for V0.
- **Why include V1 and V2 despite known limitations?** Operator choice. V1 is useful when a human is in-loop frequently saying "remember this"; some teams will be. V2 is useful as a forward path even though the recall side doesn't yet consume its fields — the prompt text is the durable artifact, and a future PR can wire the fields through without re-running daydream. Documenting the limitations in `.env.example` per-variant comments is more honest than silently omitting variants.

## Tradeoffs and risks

- **V2 ships with a known parser gap.** `_build_memory_item` (`_extract.py:275-322`) reads only `{content, tags, relevancy}`. V2's `keywords` and `context` are silently dropped — the LLM produces them but they never reach `MemoryItem` or the recall surface. ADR explicitly calls this out + the `.env.example` warning calls it out. Follow-up PR is needed to either extend `MemoryItem` or route the fields via `metadata`. Selecting V2 today gets you the prompt-side observability but no recall-side benefit.
- **V1 produces effectively-empty extraction on autonomous-agent workloads.** Per the bench sweep, V1 kept 0/27 memories on bench transcripts (which are an autonomous claude-haiku-4-5 solving pytest issues). Operators selecting V1 for a non-interactive workload will see no memory accumulation. `.env.example` warning explicitly flags this.
- **Cross-prompt structural asymmetry persists.** The CONTRADICTION and GOVERNANCE prompts (Jobs 2/3) have their own structural choices (no escape valve, no cap advertisement). Adding variants to EXTRACTION does not address that asymmetry. Out of scope.
- **Sha256 pin churn.** Now 4 pins for the 4 variants instead of 1. Every prompt change requires a pin rotation. Acceptable cost; the drift-detection value scales linearly with the number of variants.
- **Negative-substring contract scales.** The existing `test_extraction_prompt_forbids_job2_job3_vocab` -style contract now needs to apply to all 4 variants. Encoded as `test_extraction_variants_forbid_job2_job3_vocab` (loops over `_EXTRACTION_VARIANTS`).
- **The `_EXTRACTION_VARIANTS` registry is the single source of truth** for which variants exist. `list_extraction_variants()` reflects it. A future fifth variant needs only a new constant + registry entry + sha256 pin + per-variant test stanza.

## Consequences

- Operators can select any of V0/V1/V2/V3 via `.env` (or shell-exported `DREAM_EXTRACTION_VARIANT`) without source edits. The bench harness's `_add_dream_env` already forwards the env var.
- The `EXTRACTION_SYSTEM_PROMPT` constant (V0) is preserved + still pinned at `b2f8f69b…`. The three existing pin sites (`test_prompts.py:89`, `test_extract.py:43`, `test_worker_governance.py:2089`) stay green.
- 16 new tests in `test_prompts.py` cover: per-variant sha256 pins (4), variant-distinctness (1), registry completeness (1), V0-backward-compat-identity (1), selector default/arg/env/case-insensitive/empty-falls-back/unknown-raises (6), and per-variant content invariants (envelope framing / json-only / negative-substring / V1 STRICT-framing / V2 schema-extension / V3 SWE-framing / length-sanity) (8 covering all four).
- `.env.example` now documents the env var with all 4 variants commented + per-variant warnings.
- `test_extract.py:2434-2450` AST import-set audit gains one expected entry: `memeval.dreaming.prompts.get_extraction_prompt`. No other downstream consumers of `_extract.py` are affected.

## Open items

- **Wire V2's `keywords` and `context` fields to the recall path.** Either extend `MemoryItem` (schema change → contract-ADR territory) or route via `metadata` dict (no schema change). Follow-up PR; needs collaboration with storage/router code (Brent's domain).
- **Promote a variant to default?** Premature today (no bench-side measurement of FWT). Revisit when the bench either (a) clears the floor effect at a higher-capability agent model, or (b) we accept substrate-side ranking as sufficient evidence. If V3 stays best on substrate AND the recall side eventually consumes V2's fields, a successor ADR could supersede ADR-dreaming-004 and switch the default.
- **V1's zero-yield on autonomous transcripts** is a structural finding worth documenting beyond this ADR — possibly a future "when to use V1" guide or a per-domain prompt routing layer.
- **Cross-provider model behavior** of variants is uncharacterized. All bench data is on `deepseek/deepseek-v4-flash`. ling-2.6-flash (the codebase default daydream model) may behave differently — V2's parse-fail rate especially might shift on a model with different output verbosity priors.
