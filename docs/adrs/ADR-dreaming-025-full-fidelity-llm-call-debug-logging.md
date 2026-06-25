# ADR-dreaming-025 — full-fidelity `daydream.llm_call` debug logging

- **Status:** Accepted
- **Date:** 2026-06-24
- **Owner:** Scott (dreaming)
- **Contract:** false (a new diary event consumed only by developers debugging the pipeline; no production code reads it)
- **Supersedes:** none; overrides the privacy-conservative carry-over framing that shaped [ADR-dreaming-023](ADR-dreaming-023-selectable-extraction-prompt-variants.md) + PR #137 (`daydream.prompt_resolved` identity-only)

## Context

The 2026-06-24 team sync called out the production "zero saved items" symptom: the plugin's Stop hook fires `daydream-cli` repeatedly but no memories accumulate, and the team has no visibility into what's actually happening at the model boundary. Action item: *"Payload and prompt sent to DeepSeek (log input + output)"* + *"Final memory object printed to CLI as JSON."*

PR #137 partially addressed this with:
- `DREAM_DEBUG=1` stdout mirror in `events.emit()` (any diary event also goes to stdout)
- `daydream.prompt_resolved` event per chunk carrying prompt IDENTITY (variant + sha256 + char_count + model)
- `daydream.memory_written` extended with the kept-memory content/tags/relevancy

But identity-only logging does NOT meet the sync's actual requirement. When debugging "why does the model return nothing useful?" the developer needs to see:
1. The **full system prompt body** (4 KB) actually sent
2. The **full user content** (the envelope-wrapped redacted transcript chunk)
3. The **full raw model response** text — even when it's empty (the #133-shape failure) or malformed (parse failures)

The carry-over handoff memory that informed PR #137 explicitly warned against logging full prompt + content because *"raw user/transcript content [is] privacy-sensitive even after redaction"* and *"the diary becomes a forensic oracle of what redaction missed."* Those concerns are real for any operator-facing or production-shipped surface. They are NOT load-bearing for a developer-only debug surface on a local-only diary file.

## Decision

Add `daydream.llm_call` to the dreaming module's event vocabulary, emitted from `_extract.extract_memories` UNCONDITIONALLY after every `client.complete()` returns. The event carries:

- `session_id` — for diary file attribution
- `variant`, `prompt_sha256` — correlate with the lightweight `daydream.prompt_resolved` breadcrumb
- `system_prompt` — full system prompt text (~4 KB for V0; identity-equivalent to `prompt_sha256`)
- `user_content` — full envelope-wrapped redacted user content sent as the user message (the redacted transcript chunk, with the nonce-tagged transcript framing)
- `response_text` — verbatim `completion.text` (may be empty on ADR-012 failure path; may be malformed on parse-failure path)
- `tokens_in`, `tokens_out`, `cost_usd`, `model` — operational metadata for cost + latency analysis

The event fires BEFORE the empty-text early-return at `_extract.py:166` so the diagnostic surface captures the #133-shape failure (empty completion) AND the malformed-response cases. If `client.complete()` itself raises an exception, the event does not fire — the engine's outer try/except catches it via `daydream.chunk_error`; the lightweight `daydream.prompt_resolved` breadcrumb (which fires BEFORE the call) is the marker that the call was at least attempted.

`daydream.prompt_resolved` is RETAINED, not replaced. The two events are complementary:
- `prompt_resolved`: lightweight per-chunk identity breadcrumb. Cheap to grep. Carries no PII.
- `llm_call`: full-fidelity per-call debug payload. Heavy. Carries everything the LLM saw and emitted.

`DREAM_DEBUG=1` (PR #137) mirrors both to stdout. The events.py `emit()` does not need any change.

## Rationale

- **Why a new event vs extending `daydream.chunk_extracted`?** `chunk_extracted` is a lean summary record (n_items + tokens + cost + model) — useful as a per-chunk timeline grep. Stuffing 4–10 KB of prompt/content/response into it would make the field summary unreadable. Two events with distinct purposes is cleaner: grep `chunk_extracted` for the timeline, grep `llm_call` for the debug payload.
- **Why fire even on empty/malformed response?** The whole point of the event is to surface what the model returned when extraction fails. Skipping it on failure would defeat the purpose.
- **Why no second-pass redaction at the emit seam?** Mirroring the rejection-path / kept-memory pattern from PR #108 / ADR-024 would degrade the diagnostic value (a developer debugging "what did the model actually echo?" needs to see what the model actually echoed). The diary is local-only per [ADR-dreaming-011](ADR-dreaming-011-expanded-redaction-scope.md) §Policy and dev-only per this ADR. ADR-005's input-side `redact()` still runs, so the `user_content` field is the redacted transcript — not the raw user input. The exposure is "whatever LLM-echoed text slipped past input redaction, persisted to a local diary readable only by the developer running the bench." The user explicitly accepted this posture at the 2026-06-24 sync clarification.
- **Why no chunk_id field?** `extract_memories` doesn't own the cursor — the engine does. Adding a chunk_id parameter would be a signature change for a debug surface; correlate on `session_id` + timestamp instead.

## Tradeoffs & risks

- **Diary file size.** Each `llm_call` record is ~10–50 KB (system prompt + envelope + content + response). At 30+ daydream calls per bench session × 20+ sessions per bench, a single bench can produce 10–30 MB of diary text per session. The 30-day TTL sweep from [ADR-dreaming-015](ADR-dreaming-015-filesystem-state-management.md) §2 reclaims it eventually, but day-to-day disk usage during heavy bench-runs grows ~100× vs the identity-only world. Acceptable for dev-only; would not be acceptable in production.
- **Privacy posture is now per-environment.** The diary is no longer "safe to share with a colleague" by default — it contains whatever LLM-echoed user-shaped text the bench transcripts produced. Operators who run the bench against private code (the bench's astropy/pytest repos are open-source so today this is moot) need to know the diary contains that source content. Policy consequence below.
- **DREAM_DEBUG=1 stdout balloons proportionally.** With `DREAM_DEBUG=1` on, every `llm_call` record also lands on stdout. A `daydream-cli` invocation that previously emitted ~1 KB of stdout now emits 20–50 KB. Consumers piping the stream (the replay tool, Speaker D's router evaluator) need to budget for this — not a problem, but worth flagging.
- **Forensic oracle risk for the rejection diary persists** but doesn't get worse here: the existing `daydream.candidate_rejected` already carries a 100-char snippet of LLM-echoed candidate content (with ADR-005 + PR #108's second-pass `redact()`). `llm_call`'s `response_text` may carry a SUPERSET of that content (e.g., the full JSON the LLM emitted including the `rejected` array). The diagnostic value justifies the wider surface.
- **No CI guardrail prevents accidental publication of diary files.** The diary lives under `$MEMORY_STORE/dream/`. If an operator commits or uploads that directory, the LLM-echoed content leaks. Existing `.gitignore` rules cover the typical `$MEMORY_STORE` location (under `results/.../_memory/`); developers using custom basedirs need to know. Documented as a policy consequence.

## Consequences for the build

- **Policy consequence — diary handling.** The dreaming diary files (`<basedir>/dream/<session>.daydream-events.jsonl`) now contain full LLM prompts + responses. Treat them like raw `.env` files: never commit, never upload, never paste into a public issue. The `.gitignore` for `results/.../_memory/` covers the typical bench location; operators using custom `$MEMORY_STORE` outside that pattern are responsible for their own gitignore.
- **Policy consequence — DREAM_DEBUG=1 streams.** Same caveat — `DREAM_DEBUG=1` stdout now contains full LLM payloads. Don't paste stdout into a public channel. Pipe to a local file (e.g., `> /tmp/debug.jsonl`) for inspection.
- **Policy consequence — bench publication.** If we publish a bench artifact (per `eval/` workflow), the diary files MUST be stripped from the artifact bundle before publication. The bench result file itself (`results/*/swe_bench_cl-*.json`) is safe — it doesn't include diary content. The artifact-bundling step must explicitly exclude `_memory/.cookbook-memory/dream/`.
- **Non-consequence — frozen contract unchanged.** No change to `MemoryItem`, `MemoryStore`, the recall path, or any other downstream consumer. `daydream.llm_call` is a debug-only event; nothing in production code reads it.
