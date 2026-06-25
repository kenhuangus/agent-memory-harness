# ADR-dreaming-026 — `format_chunk` noise filter before redaction + LLM

- **Status:** Accepted
- **Date:** 2026-06-25
- **Owner:** Scott (dreaming)
- **Contract:** true (changes what the LLM sees per chunk AND what the redaction audit's `pre` field captures — every downstream consumer that reads the audit file or DREAM_DEBUG stream sees structured text, not raw JSONL)
- **Supersedes:** none (extends the engine's per-chunk processing pipeline established in [ADR-dreaming-013](ADR-dreaming-013-cursor-advance-ordering.md))

## Context

Claude Code session JSONL transcripts carry significant noise that bloats LLM context and dilutes the prompt's signal:

- **`queue-operation` events** — bracket every prompt enqueue/dequeue. ~2 lines per turn, mostly internal scheduler bookkeeping.
- **`attachment` events** — deferred tool listings and agent configs; often carry kilobytes of JSON that the extraction prompt never benefits from seeing.
- **`last-prompt` markers** — end-of-turn breadcrumbs without content.
- **`system` events** — hook acknowledgments and harness state.
- **Repeated metadata on every event** — `sessionId`, `uuid`, `parentUuid`, `permissionMode`, `userType`, `entrypoint`, `cwd`, `version`, `gitBranch` are re-serialized per line.

The forensic snapshot from `vbranch-main-b28b7af6` (entry 12 of KB-dreaming) demonstrated this concretely: 25 successful `daydream.chunk_extracted` events but only 2 `daydream.memory_written` — V0 MODERATE prompt was working as designed, but its INCLUDE/REJECT examples were anchored against turn-shaped content (the model's `assistant` / `tool_use` / `tool_result` blocks) that was buried under per-line metadata noise. Same finding from the substrate-sweep prep work for [ADR-dreaming-023](ADR-dreaming-023-selectable-extraction-prompt-variants.md): bench-data parse-fail rate on V2 was 0% but on the chat-data sweep it was 13% — the chat-data sweep ran on richer-noise inputs.

The user authored a `transcript_formatter.py` script (originally a CLI tool that rendered a `.jsonl` file as a structured log to stdout). The dispatch logic — collapse `queue-operation` / `attachment` / `last-prompt` / `system` to one-line markers, render model turns as `━━━ #N [ts] ROLE (model) ━━━` headers with per-block `[text]` / `[thinking]` / `[tool_use: name]` / `[tool_result]` / `[image]` annotations — is exactly the shape the LLM benefits from seeing. Integrating it as a pre-pass in the daydream engine compresses noise before redaction, which reduces both LLM token cost AND the prompt's signal-to-noise ratio.

## Decision

Add `format_chunk(jsonl_text: str, limit: int = 0) -> str` to `eval/memeval/dreaming/transcript_formatter.py` (a minimal-refactor library entry point sharing the parser's existing `_format_lines` core with `main()`). Call it from `engine.daydream` at the point where `chunk.decode(...)` produces `chunk_text` (today `engine.py:141`), BEFORE `redact_with_counts` runs:

```python
chunk_text = chunk.decode(errors="replace")
if os.environ.get("DREAM_NOISE_FILTER", "1") != "0":
    from memeval.dreaming.transcript_formatter import format_chunk
    bytes_raw = len(chunk_text)
    chunk_text = format_chunk(chunk_text, limit=int(os.environ.get("DREAM_PARSER_LIMIT", "0") or "0"))
    bytes_formatted = len(chunk_text)
    emit("daydream.noise_filtered", session_id=..., chunk_id=..., bytes_raw=..., bytes_formatted=..., ratio=...)
    if not chunk_text.strip():
        return  # all lines filtered out — same early-return semantics as raw-empty chunk
redacted, detected = redact_with_counts(chunk_text)
```

Three knobs:

- **`DREAM_NOISE_FILTER`** (default `"1"`) — set to `"0"` to bypass the filter entirely and pass raw JSONL through to redaction. Used by the existing dreaming test suite (via `tests/conftest.py` autouse fixture) so legacy tests with inline non-JSONL log content still exercise their intended code paths.
- **`DREAM_PARSER_LIMIT`** (default `"0"`) — per-block character cap matching the parser's CLI `"full"` mode (no truncation). Set to a positive int to cap each `[text]` / `[tool_use] input` / `[tool_result]` block to that length.
- **Existing `DREAM_DEBUG`** (from [ADR-dreaming-025](ADR-dreaming-025-full-fidelity-llm-call-debug-logging.md)) — the `daydream.llm_call` event already captures both the formatted `user_content` and the raw `response_text`, so the developer-debug surface sees exactly what the LLM saw.

A new event `daydream.noise_filtered` fires once per chunk when the filter runs, carrying `bytes_raw` / `bytes_formatted` / `ratio` — operational visibility for the replay tool + DREAM_DEBUG stream to characterize compression effectiveness across workloads.

## Rationale

- **Why integrate the user's script vs invent a new noise filter?** The parser already has a complete dispatch over CC-native event types — built and battle-tested by the user. Reimplementing would be redundant; importing it is a 10-line library extraction.
- **Why pre-redaction, not post-redaction?** Two reasons. (a) The redactor's regex patterns (per [ADR-dreaming-011](ADR-dreaming-011-expanded-redaction-scope.md)) operate on plain text — running them over the formatted output is the same surface as running them over raw JSONL minus the noise. (b) Redaction is the slow path; reducing the input size cuts redaction work too.
- **Why an opt-out flag rather than always-on?** The replay tool needs to be able to A/B with-vs-without to characterize the filter's effect on extraction yield. The forensic-debugging path benefits from being able to see raw JSONL if the filter is suspected of hiding signal.
- **Why `DREAM_PARSER_LIMIT` default `0` (no truncation)?** Truncating per-block content would weaken the extraction prompts' ability to anchor on examples — specifically V3's REJECT block which references raw shapes like `+    return x + 1` and `pytest output: 12 passed, 1 failed`. The default preserves full content; operators can opt into truncation for cost reasons if their LLM context budget is tighter.
- **Why early-return when filter outputs empty?** Wasted LLM call. The raw-empty case at `engine.py:137-138` already early-returns; this is the same semantics applied to the post-filter case. Cursor preservation matches: no advance on early-return, next call retries.
- **Why preserve the parser's CLI form?** The user retains the standalone `transcript_formatter.py <file.jsonl> "full"` invocation for hand-formatting any transcript outside the daydream pipeline — useful for forensic inspection. The CLI and library forms share the same `_format_lines` core (single source of truth).

## Tradeoffs & risks

- **The LLM no longer sees raw JSONL.** Every extraction-prompt variant (V0/V1/V2/V3 per [ADR-dreaming-023](ADR-dreaming-023-selectable-extraction-prompt-variants.md)) was authored against raw input. The reformatted shape preserves all model-turn content with explicit `[text]` / `[tool_use]` / `[tool_result]` markers, but the REJECT examples in V3 specifically (`pytest output: 12 passed, 1 failed`, raw diff lines) are now wrapped in `[tool_result] ...` markers and may lose some pattern-match anchoring. The substrate-sweep rankings in ADR-023 were measured on raw input; they may shift after the filter lands.
- **Audit `pre` field is now formatted, not raw.** `_write_audit_fail_open` at `engine.py:144-150` records `pre=chunk_text` — that's the post-filter value when the filter is on. The audit's purpose (per [ADR-dreaming-005](ADR-dreaming-005-v1-inline-redaction.md)) is to detect FN/FP in redaction; the filter doesn't change what redact() sees, but it does change what gets written to the audit as the "pre" snapshot. For forensic replay of a failed redaction case, the operator needs to know to set `DREAM_NOISE_FILTER=0` to reproduce raw-input behavior.
- **New failure mode: malformed JSONL lines are silently skipped.** Pre-filter, the engine sent malformed bytes to the LLM as-is (`json.loads` was inside `_extract`, after the LLM call). Post-filter, malformed lines vanish before reaching the LLM. Consistent with the engine's existing "tolerate bad bytes" posture, but slightly narrower: an all-malformed chunk now triggers the early-return path rather than a `chunk_skipped_unavailable_llm` (which would have fired after the LLM responded to garbage). Net: cheaper failure, equally non-blocking; arguably better signal.
- **Test suite divergence.** The existing dreaming test suite uses inline non-JSONL log content in many places (e.g. `log_path.write_text("content\n")`). The conftest autouse fixture (`_disable_noise_filter_by_default`) sets `DREAM_NOISE_FILTER=0` so those tests inherit the pre-filter behavior. New filter-specific tests opt back in with `monkeypatch.setenv("DREAM_NOISE_FILTER", "1")`. This is a documented divergence between test-default and production-default; the alternative (rewriting every test fixture to be JSONL-shaped) was higher-touch for the same observable outcome.
- **Process-local `LIMIT` mutation.** The parser's existing pattern stores `LIMIT` as a module-level global; `_format_lines` mutates it for the duration of the call. The function uses `try/finally` to restore the prior `LIMIT` on exit (defensive against exceptions leaking state to subsequent in-process calls), but two threads in the same process invoking `format_chunk` concurrently with different limits would still race on the global. Safe under the daydream-cli process-per-invocation model and under the replay tool's sequential in-process calls; the deferred true fix (thread `limit` through `short()` + `render_blocks()`) would change the parser's existing scheme and was skipped to honor the user's "keep the parser's scheme intact" instruction. Worth re-litigating if concurrent in-process use ever ships.
- **Lost ability to extract from non-CC-native event shapes.** If a future event-type ships that the parser doesn't recognize (no `message` key, unknown `type`), it falls into the `(no message)` branch and emits one descriptive line. The original content of that event is gone from the LLM's view. Acceptable while the dispatch keeps up with CC's event vocabulary; needs a per-version regression test if CC adds new shapes.

## Consequences for the build

- **Policy consequence — operator debugging.** When investigating a `daydream.memory_written`-yield regression, the first thing to check is whether the filter is in play: grep diary for `daydream.noise_filtered` events on the affected sessions. If `ratio` is unusually low (<0.3), the filter is stripping more than expected — possibly indicating a malformed-chunk pile-up. If `daydream.noise_filtered` is absent, the filter was bypassed (`DREAM_NOISE_FILTER=0` was set somewhere upstream).
- **Policy consequence — extraction-prompt revisions.** Any future change to the V0/V1/V2/V3 prompts' INCLUDE/REJECT examples should consider the FORMATTED shape the LLM sees, not the raw JSONL. The REJECT examples in V3 that reference raw diff lines may want refreshing to reference `[tool_result]`-wrapped forms.
- **Policy consequence — substrate sweeps.** The substrate-sweep methodology in ADR-023 should be re-run on the post-filter input shape before any future "promote a variant to default" decision. Per-variant yields may shift.
- **Contract consequence.** The **source of truth** for what the LLM sees per chunk is now `transcript_formatter.format_chunk(raw_chunk_text, limit=int(DREAM_PARSER_LIMIT))` when `DREAM_NOISE_FILTER != "0"`, applied at `engine.py:141` between `chunk.decode()` and `redact_with_counts()`. The **shape** is unchanged at the type level (`str → str`); the **semantic invariant** added: when the filter runs, the LLM input is structured text with explicit per-block markers, not raw JSONL. **Exhaustive consumers** that benefit: the extraction prompts (V0/V1/V2/V3 — they get higher-signal input), the redaction layer (smaller surface to scan), the `daydream.llm_call` event's `user_content` field (per [ADR-dreaming-025](ADR-dreaming-025-full-fidelity-llm-call-debug-logging.md) — operators see the formatted form), and the audit file's `pre` snapshot (operators must opt out of the filter to see raw bytes). Non-affected: the engine's cursor-advance ordering (ADR-013, unchanged), the per-session lock (ADR-014, unchanged), the rotation-detection mechanism (ADR-013 §sanity-check, unchanged — operates on raw bytes via `fp.seek(0); fp.read(64)`).
