# DAYDREAM_SELECTIVE_RUBRIC.md — Daydream write-time selective extraction

**Scope.** A single-PR, prompt-and-parser refinement to `eval/memeval/dreaming/`
that:

1. Rewrites `EXTRACTION_SYSTEM_PROMPT` (`prompts.py:36-74`) to enforce MODERATE
   write-time selectivity ("would a future session benefit from this fact?")
   with explicit keep/drop examples calibrated to the threshold.
2. Extends the LLM output schema with a co-equal top-level array
   `rejected: [{content_snippet, rationale}]` carrying per-candidate drop
   reasoning.
3. Extends `extract_memories` (`_extract.py:80-160`) to parse the `rejected`
   array, emit one `daydream.candidate_rejected` event per surviving rejection
   row, and account malformed rejection rows into the existing
   `chunk_partial_parse` event (extended with `memories_n_kept`,
   `memories_n_dropped`, `rejected_n_kept`, `rejected_n_dropped` kwargs —
   the kwarg rename is a documented breaking change).
4. Rotates `_SYSTEM_PROMPT_SHA256` literals in BOTH `tests/test_extract.py:43`
   AND `tests/test_prompts.py:89`.
5. Adds calibration fixtures (stub LLM only — NOT real LLM judgment),
   substring contract, per-event audit, backward-compat, malformed-row, and
   AST event-allow-set tests.

The pass runs INSIDE the existing Daydream `extract_memories` call path — no
new module-level seam, no new envelope-template variant, no new wrapper
function. The LLM judgment surface is the `rejected` array on the existing
extraction call (NOT a separate LLM call). Caller surface (`extract_memories`
signature) is UNCHANGED. Dream side is UNCHANGED. Job 3 governance is
UNCHANGED.

**Bench-signal acknowledgement (preamble pin per plan §2 dispatcher #10).**
This PR ships under the framing "MAY improve, may not." Both plausible
bench axes — store-size reduction AND recall hit-rate lift — are unverified
in this PR. The rubric grades on ARCHITECTURAL FIDELITY (prompt-contract
preservation, parse-isolation discipline, event-allow-set hygiene, backward
compatibility), NOT on downstream metric movement. A PR without a measured
bench delta is NOT FAIL on bench grounds; the grading surface is the criteria
below.

**LLM-trust posture (preamble pin per plan §2 dispatcher #7).** v1 TRUSTS LLM
JUDGMENT on the keep/drop decision. The LLM's classification of any
individual candidate as keep-worthy or rejection-worthy is NOT validated
against ground truth. Operator-facing forensics live in the per-candidate
`daydream.candidate_rejected` event stream; mis-drops are observable but
not auto-recoverable. This is an accepted v1 posture, not a defect. The
test suite calibrates the PARSE-AND-EMIT PIPELINE via a stub LLM with
canned `{memories, rejected}` payloads; it does NOT assert correctness of
the LLM's judgment on natural transcripts.

**Judgment-quality grading explicit (halliday AMENDMENT A5).** Whether
MODERATE selectivity actually works — i.e. whether the LLM judges
correctly under the new prompt — is NOT graded by unit test. It is
measured post-PR via the rejection-event stream against ad-hoc seeded
transcripts and tuned by editing the prompt's keep/drop example body
(the surrounding substring contract holds). A reviewer asking "does the
LLM judge correctly?" is answered by "we observe via the diary and tune
the prompt examples"; that surface is the eval surface, not a passing
test. Unit tests grade: the parse-and-emit contract, the substring
contract on the prompt, the cap/truncation/backward-compat invariants,
the second-pass redaction at the emit seam, and the per-chunk cap.

**PR-description checklist (halliday AMENDMENT A7).** The PR description
MUST contain a paragraph with FOUR pinned statements:
  1. The selectivity threshold is MODERATE.
  2. The operator-facing test is "would a future session benefit from
     this fact?"
  3. The rejection-event diary is the calibration surface (operators
     tune the prompt examples by reading it).
  4. No Job 3 governance changes are bundled in this PR (independent
     layers per dispatcher §3).
Pinned by §J11 (new criterion).

**Layer-independence pin (preamble pin per plan §2 dispatcher #3).**
Daydream selective extraction and Job 3 governance are INDEPENDENT layers.
Daydream filters per-session at write-time ("does this candidate deserve
storage?"). Job 3 classifies whole-store at sweep-time ("was this stored
item ever useful? does it now contradict?"). Both layers REMAIN; their
inputs differ; their timings differ. The substring contract (§C) FORBIDS
Job-3-class vocabulary (`must_know`, `must_do`, `blacklist`) appearing in
`EXTRACTION_SYSTEM_PROMPT` — bleed across layers is a substantive
contract violation, not a cosmetic one.

**Out of scope** (explicit, do not grade against):

- Changes to `worker.py` Job 3 governance pass. Daydream selective extraction
  is per-session write-time; Job 3 is whole-store sweep-time. Independent
  layers (dispatcher §3).
- Changes to `LLMClient` / `Completion` / `RedactedText` surfaces (ADR-006,
  ADR-010 hold — plan §1 Out-of-scope).
- Envelope-template change. `_ENVELOPE_TEMPLATE_SHA256` does NOT rotate. No
  new wrapper function — the 3-wrapper AST audit at `test_extract.py:679-720`
  does NOT need extension.
- New ADR. ADR-005 (durable-fact target), ADR-006 (LLMClient seam), ADR-010
  (RedactedText boundary), ADR-013 (cursor non-advance on LLM unavailable),
  ADR-009 (events shim) all hold without amendment. The selectivity refinement
  lives in the rubric preamble + PR description, not in a new ADR
  (plan §5 decision #5).
- Cross-domain coordination. CODEOWNERS for `eval/memeval/dreaming/` is the
  dreaming domain alone; no in-flight PR touches the directory.
- New `RedactedText` wrapping of rejection-event kwargs. `session_id`,
  `content_snippet`, `rationale`, `batch_index` are plain Python types at the
  emit site (events are local-only per ADR-009; the redaction boundary is at
  the LLM-call seam, not at the event-emit seam).
- New env var. No `DAYDREAM_*` config knob is introduced by this PR.
- A separate `daydream.chunk_partial_parse_rejected` event. The single
  `chunk_partial_parse` event is EXTENDED with new kwargs (plan §3.4); this
  is a deliberate, documented breaking change to the kwarg shape (plan §5
  decision #9).
- Bench claim. The PR framing is "may improve, may not" (plan §2 dispatcher
  #10).
- A new top-level third-party import in `_extract.py`. Stdlib +
  dreaming-internal only — same posture as the current module.

**Targets.**

- `eval/memeval/dreaming/prompts.py` — `EXTRACTION_SYSTEM_PROMPT` REWRITTEN
  IN PLACE at lines 36-74. `_ENVELOPE_TEMPLATE` UNCHANGED.
- `eval/memeval/dreaming/_extract.py` — `extract_memories` body extended
  between line 135 (top-level shape validation) and line 137 (memories
  build loop) with a per-rejection-row parse + per-row event emit. Three
  new module-level constants near line 43:
  - `_REJECTION_SNIPPET_MAX_LEN: int = 100`
  - `_REJECTION_RATIONALE_MAX_LEN: int = 200`
  - `_REJECTION_MAX_PER_CHUNK: int = 50` (halliday B2 cap)
  `chunk_partial_parse` kwargs RENAMED in place: `n_kept` →
  `memories_n_kept`, `n_dropped` → `memories_n_dropped`; two new kwargs
  added: `rejected_n_kept`, `rejected_n_dropped`. A second `redact()`
  call is invoked on `content_snippet` BEFORE truncation and emit
  (halliday B1 — second-pass redaction at emit seam).
- `eval/memeval/dreaming/tests/test_extract.py` — `_SYSTEM_PROMPT_SHA256`
  literal at line 43 ROTATED. New tests added under a `# §SELECTIVE` banner.
  The existing partial-parse test updated for the kwarg rename.
- `eval/memeval/dreaming/tests/test_prompts.py` — mirror sha256 literal at
  line 89 ROTATED. The `test_extraction_prompt_unchanged_by_job2` test
  RENAMED to `test_extraction_prompt_sha256_pin_consistency_across_files`.

**Supersedes.** This is a new rubric with no predecessor. The single
breaking change that supersedes existing test infrastructure:

- The `chunk_partial_parse` kwarg shape changes from `(n_kept, n_dropped)`
  to `(memories_n_kept, memories_n_dropped, rejected_n_kept,
  rejected_n_dropped)`. The single existing consumer site
  (`test_extract.py` partial-parse test) MUST be updated in lockstep with
  the `_extract.py` rename. No `n_kept`/`n_dropped` literal remains in
  the production source after this PR.

**Preserved** (NOT changed by this PR — same surface as today):

- `_ENVELOPE_TEMPLATE` body and its sha256 (no envelope-template rotation).
- `_wrap_user_content_in_envelope` signature and behavior.
- `extract_memories` callable signature (`redacted_chunk`, `client`,
  `session_id`, `now`, `id_gen`, `max_tokens`).
- The "return `None`" semantics for ADR-013 cursor non-advance: empty
  completion, malformed top-level JSON, missing `memories` key, non-list
  `memories` — all preserve return-`None` (no rejection parse runs on the
  return-`None` paths).
- The 3-wrapper envelope AST audit at `test_extract.py:679-720`. This PR
  adds NO new envelope wrapper.
- `_build_memory_item` logic and `_ParseError` per-row isolation discipline.
- The `daydream.chunk_extracted` event kwargs and emit position (terminal,
  exactly once per successful return).
- `EXTRACTION_SYSTEM_PROMPT`'s DATA/nonce prompt-injection-defense block
  (`prompts.py:39-51`). This block is PRESERVED VERBATIM in the rewrite
  (plan §7.1 + dispatcher §8).

**Format law.** Every criterion is PASS / FAIL / N-A. No "mostly," "should,"
"approximately." Each names its verification mode — `unit test` or `shell
command` — verbatim. No compound criteria (no "and/or" in a single line; split
if needed). Verify modes are PINNED — a grader that substitutes a different
test for a named criterion has not verified the criterion.

**Open contracts pinned in this rubric** (load-bearing decisions the plan
left implementer-discretionary; resolved here by dispatcher acceptance of
plan §2 + §5 decisions):

1. **Selectivity threshold = MODERATE.** Operator test: "would a future
   session benefit from this fact?" Drop one-off chatter, command echoes,
   tentative musings, narrative continuations. Pinned by §C-SUBSTRING.
2. **Per-candidate rejection event.** ONE `daydream.candidate_rejected`
   event per surviving rejection row (mirrors Job 3's
   `dream.governance_blacklisted` per-id audit). Operators tune the prompt
   by reading the rejection stream. Pinned by §E.
3. **Backward compatibility.** Missing `rejected` key in LLM output → treat
   as empty list, NO `chunk_skipped_parse_failed` emit, zero rejection
   events fire. Wrong-type `rejected` value (e.g. `null`, string) → same
   silent fallback. Pinned by §F.
4. **Snippet cap = 100 chars AND second-pass redaction at the emit seam.**
   Tighter than rationale's 200-char cap because user-derived snippets
   carry higher residual sensitivity than LLM-authored rationales. The
   cap is a *bound* on blast radius, NOT a *fix* for residual-leak risk.
   ADR-005's redaction guarantee is INPUT-side (text passed to
   `LLMClient.complete()`); the LLM may copy unredacted tokens BACK in
   `content_snippet`. The remediation pinned here: `content_snippet`
   MUST be passed through `redact()` a SECOND time before the
   `daydream.candidate_rejected` emit (halliday BLOCKER B1 — option b).
   No `[REDACTED:*]` token may appear in any emitted snippet (a token
   surviving the second pass is acceptable; the assertion is that the
   pipeline ran). Pinned by §G + §H + §K (new section).
5. **Rationale cap = 200 chars.** Mirrors Job 2 `_RATIONALE_MAX_LEN` for
   surface symmetry. Pinned by §G + §H.
6. **Partial-parse signal — extend, do not parallel.** The existing
   `chunk_partial_parse` event is EXTENDED with `rejected_n_kept` and
   `rejected_n_dropped` kwargs and the existing `n_kept`/`n_dropped`
   kwargs are RENAMED to `memories_n_kept`/`memories_n_dropped`. One
   event name to grep; one event-allow-set entry; single-emit-per-chunk
   discipline preserved. The rename is a documented breaking change.
   Pinned by §D.
7. **Rejection events fire BEFORE the chunk-extracted summary.** Operator
   reading the diary sees per-drop reasoning, then chunk-level totals.
   Pinned by §E.
8. **sha256-pin location is dual.** Both `test_extract.py:43`
   (`_SYSTEM_PROMPT_SHA256`) AND `test_prompts.py:89` mirror pin MUST
   rotate to the same hash. Rotating one and not the other = red CI.
   Pinned by §C-SHA256.
9. **Substring contract is the contract.** The prompt body MAY evolve
   across model versions for calibration; the substring set (§C-SUBSTRING)
   MUST hold. Drift in substrings = test FAIL = explicit reviewer
   bump-or-debate path. Pinned by §C-SUBSTRING + §C-NEGATIVE.
10. **No Job-3-class vocabulary in the Daydream prompt.** `must_know`,
    `must_do`, `blacklist` MUST NOT appear in `EXTRACTION_SYSTEM_PROMPT`
    (negative substring contract). Cross-layer contamination is a
    substantive contract violation. Pinned by §C-NEGATIVE.
11. **`session_id` on rejection events is engine-supplied, never
    LLM-supplied.** Same posture as `_build_memory_item`. The LLM's
    `rejected[*]` rows MUST NOT contribute a `session_id` field;
    the worker overrides with the caller-supplied `session_id`. Pinned
    by §E.
12. **`batch_index` is 0-based and reflects the ORIGINAL position in the
    `rejected` array.** Skipped (malformed) rows still consume an index.
    Pinned by §E.
13. **Calibration tests use a stub LLM only.** Real LLM judgment is
    non-deterministic; test the parse-and-emit pipeline, not the model.
    Pinned by §H.
14. **Per-chunk rejection-event cap = 50 rows** (halliday BLOCKER B2).
    The LLM may emit at most 50 entries in `rejected` per chunk; the
    parser counts overflow into `chunk_partial_parse` as
    `rejected_n_dropped` and stops emitting events past index 49 for
    that chunk. The prompt advertises the cap to the LLM so it doesn't
    get truncated arbitrarily. The constant
    `_REJECTION_MAX_PER_CHUNK = 50` lives at module top of `_extract.py`.
    Pinned by §G + §L (new section).
15. **`rejected`-field-missing surfaces a one-shot event per session**
    (halliday BLOCKER B3). A model regression that silently stops
    emitting `rejected` would otherwise look identical to "the LLM
    judged everything keep-worthy." On the FIRST chunk per session
    where the parsed top-level dict lacks a `rejected` key (sentinel
    distinguished from `rejected: []`), the worker emits exactly ONE
    `daydream.rejected_field_missing` event with kwargs
    `{session_id}`. Subsequent same-session chunks with the same
    condition do NOT re-emit. The event name is added to the
    event-allow-set (now 6 names). Pinned by §E + §M (new section).
16. **Overlap between `memories` and `rejected` is silently dropped on
    the rejection side** (halliday AMENDMENT A1). When the LLM emits
    a kept memory whose `content` (lowercased, stripped, prefix-matched
    to `_REJECTION_SNIPPET_MAX_LEN`) matches a rejected row's
    `content_snippet` (same normalization), the rejection event is
    SUPPRESSED and the row counts into `chunk_partial_parse` as
    `rejected_n_dropped`. The kept memory wins. Pinned by §D + §N (new
    section).
17. **Truncation is observable on the event payload** (halliday
    AMENDMENT A2). When `len(raw_snippet) > _REJECTION_SNIPPET_MAX_LEN`
    OR `len(raw_rationale) > _REJECTION_RATIONALE_MAX_LEN`, the emitted
    event carries `snippet_truncated: bool` and `rationale_truncated:
    bool` kwargs respectively. The kwarg set on
    `daydream.candidate_rejected` expands from 4 keys to 6:
    `{content_snippet, rationale, session_id, batch_index,
    snippet_truncated, rationale_truncated}`. §E4 updated.
18. **Negative substring contract pins forbidden Job-2 vocabulary too**
    (halliday AMENDMENT A4). `EXTRACTION_SYSTEM_PROMPT.lower()` MUST
    NOT contain `"pairs"`, `"a_id"`, `"b_id"` (Job 2 contradiction
    schema vocabulary) in addition to Job 3's `must_know`/`must_do`/
    `blacklist`. Three layers stay clean. Pinned by §C-NEGATIVE.

---

## A. Surface — `extract_memories` returns expected shape; rejection events fire

- [ ] **A1.** With a stub `LLMClient` whose `complete()` returns a valid
      `Completion(text='{"memories":[],"rejected":[]}', tokens_in=5, tokens_out=5)`,
      `extract_memories(...)` returns `[]` (NOT `None`) and does not raise.
      **Verify:** unit test `test_extract_returns_empty_list_for_empty_memories_with_empty_rejected`.
- [ ] **A2.** With a stub returning `Completion(text='{"memories":[{"content":"x"}],"rejected":[]}', ...)`,
      `extract_memories(...)` returns a `list` of length 1 whose sole element
      is a `MemoryItem`. **Verify:** unit test
      `test_extract_returns_one_memory_when_one_memory_and_empty_rejected`.
- [ ] **A3.** With a stub returning `{"memories":[],"rejected":[{"content_snippet":"hi","rationale":"social greeting"}]}`,
      `extract_memories(...)` returns `[]` (NOT `None`) and emits exactly one
      `daydream.candidate_rejected` event. **Verify:** unit test
      `test_extract_returns_empty_list_when_memories_empty_and_one_rejection`.
- [ ] **A4.** `extract_memories(...)` callable signature is UNCHANGED from
      pre-PR: positional `redacted_chunk`; keyword-only `client`, `session_id`,
      `now`, `id_gen`, `max_tokens=2048`. **Verify:** unit test
      `test_extract_memories_signature_unchanged`.
- [ ] **A5.** With a stub returning empty `Completion("", 0, 0)`,
      `extract_memories(...)` returns `None` (ADR-013 cursor non-advance
      preserved), emits exactly one `chunk_skipped_unavailable_llm` event,
      and emits ZERO `daydream.candidate_rejected` events (rejection parsing
      lives BELOW the empty-completion guard). **Verify:** unit test
      `test_empty_completion_returns_none_and_no_rejection_events`.
- [ ] **A6.** With a stub returning malformed JSON `Completion("not json", 5, 5)`,
      `extract_memories(...)` returns `None`, emits exactly one
      `chunk_skipped_parse_failed` event, and emits ZERO
      `daydream.candidate_rejected` events. **Verify:** unit test
      `test_malformed_json_returns_none_and_no_rejection_events`.
- [ ] **A7.** With a stub returning `{"memories":[],"rejected":[{...},{...},{...}]}`
      and three valid rejection rows, `extract_memories(...)` emits exactly
      THREE `daydream.candidate_rejected` events. **Verify:** unit test
      `test_extract_emits_one_event_per_rejected_row`.

## B. JSON output schema — `memories` + `rejected` keys present; backward-compat

- [ ] **B1.** A `Completion` whose parsed JSON is `{"memories":[{"content":"x"}]}`
      (NO `rejected` key) is accepted: `extract_memories` returns
      `[MemoryItem(content="x", ...)]`, NO `chunk_skipped_parse_failed` event
      fires, and ZERO `daydream.candidate_rejected` events fire. **Verify:**
      unit test `test_missing_rejected_key_silently_accepted`.
- [ ] **B2.** A `Completion` whose parsed JSON is `{"memories":[{"content":"x"}],"rejected":null}`
      is accepted: 1 `MemoryItem` returned, NO `chunk_skipped_parse_failed`
      event fires, ZERO rejection events. **Verify:** unit test
      `test_rejected_null_silently_falls_back_to_empty_list`.
- [ ] **B3.** A `Completion` whose parsed JSON is `{"memories":[{"content":"x"}],"rejected":"oops"}`
      is accepted: 1 `MemoryItem` returned, NO `chunk_skipped_parse_failed`
      event fires, ZERO rejection events. **Verify:** unit test
      `test_rejected_wrong_type_string_silently_falls_back_to_empty_list`.
- [ ] **B4.** A `Completion` whose parsed JSON has `"memories"` as a list but
      missing entirely (i.e., `{"rejected":[{...}]}`) is REJECTED: returns
      `None`, emits `chunk_skipped_parse_failed`. The backward-compat rule
      applies ONLY to `rejected`; `memories` remains REQUIRED. **Verify:**
      unit test `test_missing_memories_key_still_returns_none`.
- [ ] **B5.** A `Completion` parsed as `{"memories":[{"content":"x"}],"rejected":[{"content_snippet":"hi","rationale":"r"}]}`
      results in `len(items) == 1` (memories) AND exactly one
      `daydream.candidate_rejected` event (rejected). The two arrays
      process independently. **Verify:** unit test
      `test_memories_and_rejected_process_independently`.

## C. Prompt contract — substring contract + sha256 pin + rejection schema

### C-SHA256. sha256 pin rotation (both files)

- [ ] **C-SHA256-1.** `hashlib.sha256(EXTRACTION_SYSTEM_PROMPT.encode("utf-8")).hexdigest()`
      equals the hex literal `_SYSTEM_PROMPT_SHA256` at
      `tests/test_extract.py:43`. **Verify:** unit test
      `test_extraction_system_prompt_sha256_pinned`.
- [ ] **C-SHA256-2.** `hashlib.sha256(EXTRACTION_SYSTEM_PROMPT.encode("utf-8")).hexdigest()`
      equals the hex literal at `tests/test_prompts.py:89` (the mirror pin).
      **Verify:** unit test
      `test_extraction_prompt_sha256_pin_consistency_across_files` (renamed
      from `test_extraction_prompt_unchanged_by_job2`).
- [ ] **C-SHA256-3.** The two hex literals at `test_extract.py:43` and
      `test_prompts.py:89` are byte-equal as strings. Rotating one and
      forgetting the other is a test failure. **Verify:** shell command
      `python3 -c "import re,pathlib; a=re.search(r'_SYSTEM_PROMPT_SHA256\s*=\s*\"([0-9a-f]{64})\"', pathlib.Path('eval/memeval/dreaming/tests/test_extract.py').read_text()).group(1); b=re.search(r'\"([0-9a-f]{64})\"', pathlib.Path('eval/memeval/dreaming/tests/test_prompts.py').read_text()).group(1); assert a==b, (a,b); print('OK')"`.

### C-SUBSTRING. Required substrings (positive contract)

- [ ] **C-SUBSTRING-1.** `"durable"` appears in `EXTRACTION_SYSTEM_PROMPT.lower()`.
      **Verify:** unit test `test_extraction_system_prompt_pins_durable_substring`.
- [ ] **C-SUBSTRING-2.** `"decisions"` appears in `EXTRACTION_SYSTEM_PROMPT.lower()`.
      **Verify:** unit test `test_extraction_system_prompt_pins_decisions_substring`.
- [ ] **C-SUBSTRING-3.** `"commitments"` appears in `EXTRACTION_SYSTEM_PROMPT.lower()`.
      **Verify:** unit test `test_extraction_system_prompt_pins_commitments_substring`.
- [ ] **C-SUBSTRING-4.** `"would a future session"` appears in
      `EXTRACTION_SYSTEM_PROMPT.lower()` (the operator-facing inclusion test,
      pinned verbatim). **Verify:** unit test
      `test_extraction_system_prompt_pins_future_session_threshold_question`.
- [ ] **C-SUBSTRING-5.** `"rejected"` appears in
      `EXTRACTION_SYSTEM_PROMPT.lower()` (the new schema key). **Verify:**
      unit test `test_extraction_system_prompt_pins_rejected_substring`.
- [ ] **C-SUBSTRING-6.** `"content_snippet"` appears in
      `EXTRACTION_SYSTEM_PROMPT.lower()` (rejection-row field name).
      **Verify:** unit test
      `test_extraction_system_prompt_pins_content_snippet_field_name`.
- [ ] **C-SUBSTRING-7.** `"rationale"` appears in
      `EXTRACTION_SYSTEM_PROMPT.lower()` (rejection-row field name; matches
      Job 2/3 convention). **Verify:** unit test
      `test_extraction_system_prompt_pins_rationale_field_name`.
- [ ] **C-SUBSTRING-8.** `"be selective"` appears in
      `EXTRACTION_SYSTEM_PROMPT.lower()` (load-bearing selectivity
      imperative). **Verify:** unit test
      `test_extraction_system_prompt_pins_be_selective_imperative`.
- [ ] **C-SUBSTRING-9.** `"data, not instructions"` appears in
      `EXTRACTION_SYSTEM_PROMPT.lower()` (PRESERVED injection-defense pin).
      **Verify:** unit test
      `test_extraction_system_prompt_preserves_injection_defense_data_pin`.
- [ ] **C-SUBSTRING-10.** `"nonce"` appears in
      `EXTRACTION_SYSTEM_PROMPT.lower()` (PRESERVED injection-defense pin).
      **Verify:** unit test
      `test_extraction_system_prompt_preserves_injection_defense_nonce_pin`.
- [ ] **C-SUBSTRING-11.** `"json only"` appears in
      `EXTRACTION_SYSTEM_PROMPT.lower()` (PRESERVED schema pin).
      **Verify:** unit test
      `test_extraction_system_prompt_preserves_json_only_pin`.
- [ ] **C-SUBSTRING-12.** `"no markdown fences"` appears in
      `EXTRACTION_SYSTEM_PROMPT.lower()` (PRESERVED schema pin). **Verify:**
      unit test `test_extraction_system_prompt_preserves_no_markdown_fences_pin`.

### C-NEGATIVE. Forbidden substrings (negative contract — no Job-3 vocab)

- [ ] **C-NEGATIVE-1.** `"must_know"` does NOT appear in
      `EXTRACTION_SYSTEM_PROMPT.lower()` (Job 3 class vocabulary; layer
      independence). **Verify:** unit test
      `test_extraction_system_prompt_forbids_must_know_vocab`.
- [ ] **C-NEGATIVE-2.** `"must_do"` does NOT appear in
      `EXTRACTION_SYSTEM_PROMPT.lower()`. **Verify:** unit test
      `test_extraction_system_prompt_forbids_must_do_vocab`.
- [ ] **C-NEGATIVE-3.** `"blacklist"` does NOT appear in
      `EXTRACTION_SYSTEM_PROMPT.lower()`. **Verify:** unit test
      `test_extraction_system_prompt_forbids_blacklist_vocab`.
- [ ] **C-NEGATIVE-4.** `"pairs"` does NOT appear in
      `EXTRACTION_SYSTEM_PROMPT.lower()` (Job 2 contradiction-schema
      vocabulary; halliday A4). **Verify:** unit test
      `test_extraction_system_prompt_forbids_pairs_vocab`.
- [ ] **C-NEGATIVE-5.** `"a_id"` does NOT appear in
      `EXTRACTION_SYSTEM_PROMPT.lower()` (Job 2 vocabulary; halliday A4).
      **Verify:** unit test
      `test_extraction_system_prompt_forbids_a_id_vocab`.
- [ ] **C-NEGATIVE-6.** `"b_id"` does NOT appear in
      `EXTRACTION_SYSTEM_PROMPT.lower()` (Job 2 vocabulary; halliday A4).
      **Verify:** unit test
      `test_extraction_system_prompt_forbids_b_id_vocab`.

### C-SCHEMA. Rejection schema pinned in prompt text

- [ ] **C-SCHEMA-1.** `EXTRACTION_SYSTEM_PROMPT` contains the literal
      substring `'"rejected"'` (with surrounding double-quotes — schema
      shows it as a JSON key). **Verify:** unit test
      `test_extraction_system_prompt_pins_rejected_as_quoted_json_key`.
- [ ] **C-SCHEMA-2.** `EXTRACTION_SYSTEM_PROMPT` contains the literal
      substring `'"content_snippet"'` (as a JSON key). **Verify:** unit
      test `test_extraction_system_prompt_pins_content_snippet_as_quoted_json_key`.
- [ ] **C-SCHEMA-3.** `EXTRACTION_SYSTEM_PROMPT` contains text indicating
      that BOTH `"memories"` and `"rejected"` are required top-level keys
      (substring `"required"` adjacent to either key name within 100 chars,
      case-insensitive). **Verify:** unit test
      `test_extraction_system_prompt_states_both_keys_required`.
- [ ] **C-SCHEMA-4.** `EXTRACTION_SYSTEM_PROMPT` documents the
      `content_snippet` length cap as `<=100` or `100 characters` (substring
      check, case-insensitive). **Verify:** unit test
      `test_extraction_system_prompt_documents_snippet_cap_100`.
- [ ] **C-SCHEMA-5.** `EXTRACTION_SYSTEM_PROMPT` documents the `rationale`
      length cap as `<=200` or `200 characters` (substring check,
      case-insensitive). **Verify:** unit test
      `test_extraction_system_prompt_documents_rationale_cap_200`.

## D. Parse isolation — malformed rows count into `chunk_partial_parse`

- [ ] **D1.** With a stub returning `{"memories":[{"content":"kept"},"garbage",{"not":"valid"}],"rejected":[]}`,
      `extract_memories` returns a list of length 1 (the valid memory)
      AND emits exactly ONE `chunk_partial_parse` event AND emits ZERO
      `chunk_skipped_parse_failed` events (partial parse is NOT fatal —
      ADR-013 cursor still advances). **Verify:** unit test
      `test_malformed_memories_row_counts_into_chunk_partial_parse`.
- [ ] **D2.** The `chunk_partial_parse` event emitted in §D1 has kwargs
      `{memories_n_kept: 1, memories_n_dropped: 2, rejected_n_kept: 0,
      rejected_n_dropped: 0}`. The kwarg set is exactly those four keys.
      **Verify:** unit test
      `test_chunk_partial_parse_kwarg_set_exact_four_keys`.
- [ ] **D3.** With a stub returning `{"memories":[{"content":"kept"}],"rejected":[{"content_snippet":"a","rationale":"b"},"garbage",{"content_snippet":"c"}]}`,
      `extract_memories` returns a list of length 1 AND emits exactly ONE
      `daydream.candidate_rejected` event (the single valid rejection row)
      AND emits exactly ONE `chunk_partial_parse` event AND emits ZERO
      `chunk_skipped_parse_failed` events. **Verify:** unit test
      `test_malformed_rejected_row_counts_into_chunk_partial_parse`.
- [ ] **D4.** The `chunk_partial_parse` event emitted in §D3 has kwargs
      `{memories_n_kept: 1, memories_n_dropped: 0, rejected_n_kept: 1,
      rejected_n_dropped: 2}`. **Verify:** unit test
      `test_chunk_partial_parse_extended_kwargs_for_rejected_drops`.
- [ ] **D5.** When BOTH `memories` AND `rejected` parse without per-row
      failures (e.g. all rows valid), the `chunk_partial_parse` event is
      NOT emitted (all-zero drop case stays silent — preserves current
      emit cadence). **Verify:** unit test
      `test_chunk_partial_parse_not_emitted_when_no_drops`.
- [ ] **D6.** When the `rejected` array contains rows missing the
      `rationale` key (e.g. `[{"content_snippet":"a"}]`), the worker
      drops the row and increments `rejected_n_dropped`. **Verify:**
      unit test `test_rejected_row_missing_rationale_dropped`.
- [ ] **D7.** When the `rejected` array contains rows where `content_snippet`
      is not a string (e.g. `[{"content_snippet":123,"rationale":"r"}]`),
      the worker drops the row. **Verify:** unit test
      `test_rejected_row_wrong_type_content_snippet_dropped`.
- [ ] **D8.** When the `rejected` array contains rows where `rationale`
      is not a string (e.g. `[{"content_snippet":"s","rationale":["r"]}]`),
      the worker drops the row. **Verify:** unit test
      `test_rejected_row_wrong_type_rationale_dropped`.
- [ ] **D9.** Source contains zero `n_kept=` or `n_dropped=` literal kwarg
      sites in `_extract.py` (the rename is complete). **Verify:** shell
      command `! grep -nE 'n_kept[[:space:]]*=|n_dropped[[:space:]]*=' eval/memeval/dreaming/_extract.py`.
- [ ] **D10.** Source contains exactly one `chunk_partial_parse` emit site
      in `_extract.py`. **Verify:** shell command
      `test "$(grep -c 'chunk_partial_parse' eval/memeval/dreaming/_extract.py)" -eq 1`.
- [ ] **D11.** Count cross-check: when `chunk_partial_parse` fires with
      a non-zero `rejected_n_kept`, the number of
      `daydream.candidate_rejected` events captured in the SAME
      `extract_memories` call equals that count. Closes RUBRIC_GAP-2
      (halliday adversarial pass). **Verify:** unit test
      `test_rejected_n_kept_equals_emitted_rejection_event_count`.
- [ ] **D12.** Count cross-check: when NO `chunk_partial_parse` fires
      (all-zero drops on both arrays), the number of
      `daydream.candidate_rejected` events equals the length of the
      input `rejected` array (no silent drop). **Verify:** unit test
      `test_no_partial_parse_means_all_rejection_rows_emitted`.

## E. Events — 4 preserved daydream/chunk events + new `daydream.candidate_rejected`

- [ ] **E1.** The complete set of `emit("...")` event-name string literals
      in `_extract.py` is EXACTLY
      `{"chunk_skipped_unavailable_llm", "chunk_skipped_parse_failed",
      "chunk_partial_parse", "daydream.chunk_extracted",
      "daydream.candidate_rejected", "daydream.rejected_field_missing"}`
      (SIX names — halliday B3 added the sixth). **Verify:** unit test
      `test_extract_event_allow_set_ast` — AST walk of `_extract.py` collecting
      `ast.Call(func=ast.Name(id="emit"), args=[ast.Constant(value=<str>), ...])`
      first-arg values into a set; assert exact equality to the six-name set.
- [ ] **E2.** AST walk in §E1 ALSO asserts that every `emit(...)` call's
      first positional argument is `ast.Constant` with a string value (no
      dynamic event names; no f-strings; no variable-bound names). A
      `False` on that guard is a separate audit failure surfaced as a
      distinct assertion message. **Verify:** unit test
      `test_extract_event_names_are_static_string_constants_ast`.
- [ ] **E3.** Each successful `extract_memories` call emits exactly ONE
      `daydream.chunk_extracted` event. **Verify:** unit test
      `test_extract_emits_exactly_one_chunk_extracted_per_success`.
- [ ] **E4.** Each `daydream.candidate_rejected` event has kwarg set
      EXACTLY `{content_snippet, rationale, session_id, batch_index,
      snippet_truncated, rationale_truncated}` (SIX keys — halliday A2
      added the two truncation flags). No extras. **Verify:** unit test
      `test_candidate_rejected_event_kwarg_set_exact`.
- [ ] **E5.** `daydream.candidate_rejected` event kwarg `session_id` equals
      the caller-supplied `session_id` argument to `extract_memories`,
      regardless of any `session_id` field present in the LLM's rejected
      payload (LLM-supplied `session_id` MUST be ignored). **Verify:**
      unit test
      `test_candidate_rejected_session_id_is_engine_supplied_not_llm`.
- [ ] **E6.** `daydream.candidate_rejected` event kwarg `batch_index` is
      `int` and 0-based: with three valid rejection rows in order, the
      three events fire with `batch_index ∈ {0, 1, 2}` (set-equal).
      **Verify:** unit test
      `test_candidate_rejected_batch_index_is_zero_based`.
- [ ] **E7.** `daydream.candidate_rejected` event kwarg `batch_index`
      reflects the ORIGINAL position in the `rejected` array — dropping
      a malformed row at index 1 still gives the row at index 2 the
      `batch_index=2` value. **Verify:** unit test
      `test_candidate_rejected_batch_index_skips_over_dropped_rows`.
- [ ] **E8.** ALL `daydream.candidate_rejected` events fire BEFORE the
      single `daydream.chunk_extracted` event in capture order.
      **Verify:** unit test
      `test_rejection_events_precede_chunk_extracted_in_capture_order`.
- [ ] **E9.** When `extract_memories` returns `None` (any abort path —
      empty completion, JSON parse error, malformed top-level), ZERO
      `daydream.candidate_rejected` events fire (rejection parsing lives
      below all return-`None` guards). **Verify:** unit test
      `test_zero_rejection_events_on_any_return_none_path`.
- [ ] **E10.** No event with name starting `daydream.candidate_` other
      than `daydream.candidate_rejected` is emitted. **Verify:** shell
      command `! grep -nE 'emit\([[:space:]]*["'\'']daydream\.candidate_(?!rejected)' eval/memeval/dreaming/_extract.py`.
- [ ] **E11.** LLM session_id injection attempt is ignored: with a stub
      returning a rejection row `{"content_snippet":"x","rationale":"y","session_id":"ATTACKER_SID"}`,
      the emitted `daydream.candidate_rejected` event's `session_id`
      kwarg equals the caller-supplied value (NOT `"ATTACKER_SID"`).
      Closes RUBRIC_GAP-3 (halliday adversarial pass). **Verify:** unit
      test
      `test_llm_attempted_session_id_injection_is_ignored_by_engine`.

## F. Backward compatibility — missing-`rejected` LLM output silently accepted

- [ ] **F1.** A stub returning `Completion(text='{"memories":[]}', ...)` (no
      `rejected` key) results in `extract_memories(...) == []` (NOT `None`)
      AND zero `chunk_skipped_parse_failed` emits AND zero
      `daydream.candidate_rejected` emits. **Verify:** unit test
      `test_missing_rejected_key_is_real_empty_extraction`.
- [ ] **F2.** A stub returning `{"memories":[{"content":"x"}]}` (no
      `rejected` key) results in `len(extract_memories(...)) == 1` AND zero
      `chunk_skipped_parse_failed` emits AND zero
      `daydream.candidate_rejected` emits. **Verify:** unit test
      `test_missing_rejected_key_does_not_block_memories_emission`.
- [ ] **F3.** The backward-compat fallback applies to wrong-type `rejected`
      values: `null`, string, number, dict, bool — all silently treated as
      empty list. **Verify:** unit test
      `test_rejected_wrong_type_fallback_covers_null_string_number_dict_bool`
      (parametrized).
- [ ] **F4.** The backward-compat fallback does NOT apply to `memories`:
      missing `memories` key OR non-list `memories` still returns `None`
      with `chunk_skipped_parse_failed`. **Verify:** unit test
      `test_backward_compat_is_rejected_only_not_memories`.

## G. Snippet + rationale caps enforced

- [ ] **G1.** When the stub returns `{"memories":[],"rejected":[{"content_snippet":"a"*500,"rationale":"b"*500}]}`,
      the emitted `daydream.candidate_rejected` event has
      `len(content_snippet) == 100` AND `len(rationale) == 200`.
      **Verify:** unit test
      `test_rejection_event_truncates_oversize_snippet_and_rationale`.
- [ ] **G2.** Truncation is a plain slice (`s[:N]`) — no ellipsis appended,
      no smart truncation, no UTF-8-aware boundary adjustment. **Verify:**
      unit test
      `test_rejection_event_truncation_is_plain_slice_no_ellipsis` —
      assert `event.kwargs["content_snippet"] == ("a" * 500)[:100]` (byte-equal).
- [ ] **G3.** `_REJECTION_SNIPPET_MAX_LEN` is the integer literal `100` at
      `_extract.py` module level. **Verify:** unit test
      `test_rejection_snippet_max_len_is_100`.
- [ ] **G4.** `_REJECTION_RATIONALE_MAX_LEN` is the integer literal `200`
      at `_extract.py` module level. **Verify:** unit test
      `test_rejection_rationale_max_len_is_200`.
- [ ] **G5.** When the stub returns a snippet of length exactly 100 (no
      truncation needed), the emitted event has `content_snippet` byte-equal
      to the input. **Verify:** unit test
      `test_rejection_snippet_at_cap_passes_through_unchanged`.
- [ ] **G6.** When the stub returns a rationale of length exactly 200, the
      emitted event has `rationale` byte-equal to the input. **Verify:**
      unit test `test_rejection_rationale_at_cap_passes_through_unchanged`.
- [ ] **G7.** When the stub returns a snippet shorter than 100 chars (e.g.
      `"hi"`), the emitted event has `content_snippet == "hi"` (no padding,
      no normalization). **Verify:** unit test
      `test_rejection_snippet_under_cap_passes_through_unchanged`.
- [ ] **G8.** When the stub returns `content_snippet` of length > 100,
      the emitted event has `snippet_truncated == True` (halliday A2).
      **Verify:** unit test
      `test_rejection_event_marks_snippet_truncated_when_oversize`.
- [ ] **G9.** When the stub returns `content_snippet` of length <= 100,
      the emitted event has `snippet_truncated == False`. **Verify:**
      unit test `test_rejection_event_snippet_truncated_false_when_at_or_under_cap`.
- [ ] **G10.** When the stub returns `rationale` of length > 200, the
      emitted event has `rationale_truncated == True` (halliday A2).
      **Verify:** unit test
      `test_rejection_event_marks_rationale_truncated_when_oversize`.
- [ ] **G11.** When the stub returns `rationale` of length <= 200, the
      emitted event has `rationale_truncated == False`. **Verify:**
      unit test `test_rejection_event_rationale_truncated_false_when_at_or_under_cap`.

## K. Second-pass redaction on `content_snippet` (halliday BLOCKER B1)

- [ ] **K1.** `_extract.py` source contains a call to `redact(...)`
      that takes the LLM-emitted `content_snippet` as input BEFORE the
      truncation slice AND BEFORE the `emit("daydream.candidate_rejected", ...)`
      call. **Verify:** unit test
      `test_rejection_content_snippet_routes_through_redact_before_emit`
      — AST walk asserts the `emit` call site has `content_snippet=`
      kwarg whose value-expression contains a `Call(func=Name(id="redact"))`
      (or a sliced result of one).
- [ ] **K2.** With a stub returning
      `{"memories":[],"rejected":[{"content_snippet":"sk-test_AKIA1234567890","rationale":"key"}]}`,
      the emitted `daydream.candidate_rejected` event's `content_snippet`
      DOES NOT contain the original substring `"AKIA1234567890"` — i.e.,
      `redact()` ran. (Uses the existing redaction pattern set; the
      assertion is that the second-pass pipeline executed, not that
      every conceivable token is caught.) **Verify:** unit test
      `test_rejection_content_snippet_second_pass_redaction_catches_aws_key`.
- [ ] **K3.** `redact()` is imported at `_extract.py` module top (the
      import allow-list in §I1 is updated to include it; the import
      MUST be from the same module that wraps user content on the input
      side). **Verify:** unit test
      `test_extract_imports_redact_at_module_top`.
- [ ] **K4.** `rationale` is NOT routed through `redact()` (LLM-authored
      prose; per plan §5 decision #6 the snippet carries higher residual
      sensitivity than the rationale). **Verify:** unit test
      `test_rejection_rationale_not_routed_through_redact` — AST walk
      asserts the `rationale=` kwarg expression at the emit site does NOT
      contain a `Call(func=Name(id="redact"))`.
- [ ] **K5.** No `[REDACTED:` substring appears in the rationale field
      of any emitted `daydream.candidate_rejected` event in the
      calibration fixtures (sanity that we are not double-redacting
      rationale by accident). **Verify:** unit test
      `test_no_redacted_token_in_calibration_rationales`.

## L. Per-chunk rejection-event cap (halliday BLOCKER B2)

- [ ] **L1.** `_REJECTION_MAX_PER_CHUNK` is the integer literal `50` at
      `_extract.py` module level. **Verify:** unit test
      `test_rejection_max_per_chunk_is_50`.
- [ ] **L2.** When the stub returns a `rejected` array of length 75
      (all valid rows), `extract_memories(...)` emits EXACTLY 50
      `daydream.candidate_rejected` events. **Verify:** unit test
      `test_rejection_cap_emits_at_most_50_events_per_chunk`.
- [ ] **L3.** When the stub returns a `rejected` array of length 75
      (all valid rows), the `chunk_partial_parse` event fires with
      `rejected_n_kept=50, rejected_n_dropped=25` (the 25 overflow rows
      count as drops). **Verify:** unit test
      `test_rejection_cap_overflow_counts_into_chunk_partial_parse`.
- [ ] **L4.** When the stub returns a `rejected` array of length 75,
      the 50 events that fire have `batch_index` values `{0..49}`
      (set-equal); indices 50..74 are NOT emitted. **Verify:** unit test
      `test_rejection_cap_emits_first_50_batch_indices`.
- [ ] **L5.** When the stub returns a `rejected` array of length 50
      (exactly at cap, all valid), 50 events fire AND
      `chunk_partial_parse` is NOT emitted (the all-zero drop case
      preserves silence). **Verify:** unit test
      `test_rejection_cap_at_exact_50_is_silent_chunk_partial_parse`.
- [ ] **L6.** `EXTRACTION_SYSTEM_PROMPT.lower()` documents the cap
      (substring `"up to 50"` or `"at most 50"` near a `rejected`
      reference). **Verify:** unit test
      `test_extraction_system_prompt_documents_per_chunk_cap_50`.

## M. `daydream.rejected_field_missing` one-shot per session (halliday BLOCKER B3)

- [ ] **M1.** When a stub returns `{"memories":[{"content":"x"}]}`
      (no `rejected` key — sentinel-distinguished from `[]`), the
      worker emits exactly ONE `daydream.rejected_field_missing` event
      with kwargs `{session_id}`. **Verify:** unit test
      `test_missing_rejected_key_emits_one_rejected_field_missing_event`.
- [ ] **M2.** When TWO chunks of the same `session_id` each return a
      payload with NO `rejected` key, the worker emits the
      `daydream.rejected_field_missing` event EXACTLY ONCE (one-shot
      per session). **Verify:** unit test
      `test_rejected_field_missing_one_shot_per_session_across_chunks`.
- [ ] **M3.** When a chunk returns `{"memories":[],"rejected":[]}`
      (explicit empty list — the LLM acknowledged the field), the
      `daydream.rejected_field_missing` event is NOT emitted. **Verify:**
      unit test
      `test_rejected_field_missing_not_emitted_when_explicit_empty_list`.
- [ ] **M4.** When a chunk returns `{"memories":[],"rejected":null}`
      or `{"memories":[],"rejected":"oops"}` (wrong-type fallback), the
      `daydream.rejected_field_missing` event is NOT emitted (the field
      WAS present; the value was wrong). **Verify:** unit test
      `test_rejected_field_missing_not_emitted_when_wrong_type`.
- [ ] **M5.** `daydream.rejected_field_missing` event kwarg set is
      EXACTLY `{session_id}`. **Verify:** unit test
      `test_rejected_field_missing_kwarg_set_exact_session_id_only`.
- [ ] **M6.** Two SEPARATE `session_id` values, each with a
      missing-`rejected` chunk, produce TWO
      `daydream.rejected_field_missing` events (one-shot is per-session,
      not per-process). **Verify:** unit test
      `test_rejected_field_missing_one_shot_is_per_session_not_global`.

## N. Memory/rejected overlap suppression (halliday AMENDMENT A1)

- [ ] **N1.** When the stub returns
      `{"memories":[{"content":"user wants Postgres"}],"rejected":[{"content_snippet":"user wants Postgres","rationale":"redundant"}]}`,
      the worker emits ZERO `daydream.candidate_rejected` events
      (overlap drops the rejection; the kept memory wins). **Verify:**
      unit test
      `test_overlap_between_memories_and_rejected_suppresses_rejection_event`.
- [ ] **N2.** The §N1 case fires `chunk_partial_parse` with
      `rejected_n_dropped >= 1` (the overlap row counts as a drop).
      **Verify:** unit test
      `test_overlap_drop_counts_into_chunk_partial_parse_rejected_n_dropped`.
- [ ] **N3.** Overlap detection is case-insensitive + whitespace-stripped
      + prefix-bounded by `_REJECTION_SNIPPET_MAX_LEN` on both sides.
      With memory content `"  USER wants Postgres  "` and rejection
      snippet `"user wants postgres"`, the overlap fires (suppression
      applies). **Verify:** unit test
      `test_overlap_detection_is_case_insensitive_and_stripped`.
- [ ] **N4.** When there is no overlap (memory `"x"`, rejection
      snippet `"y"`), both surfaces persist normally. **Verify:**
      unit test `test_non_overlapping_memory_and_rejection_both_persist`.

## H. Calibration fixtures — stub LLM only, 3 keep + 3 drop pinned cases

- [ ] **H1.** Fixture A (pure-keep): stub returns `{"memories":[{"content":"user prefers Postgres over Redis for the auth service"},{"content":"user name is Scott"},{"content":"user committed to backfill migration Friday"}],"rejected":[]}`.
      `extract_memories` returns a list of length 3; ZERO
      `daydream.candidate_rejected` events fire; ZERO `chunk_partial_parse`
      events fire. **Verify:** unit test
      `test_calibration_fixture_a_three_keeps_zero_drops`.
- [ ] **H2.** Fixture B (pure-drop): stub returns `{"memories":[],"rejected":[{"content_snippet":"User: hey","rationale":"social greeting"},{"content_snippet":"Let me think","rationale":"tentative musing, no decision"},{"content_snippet":"ls returned 3 files","rationale":"one-off command output"}]}`.
      `extract_memories` returns `[]` (NOT `None`); exactly THREE
      `daydream.candidate_rejected` events fire; ZERO `chunk_partial_parse`
      events fire. **Verify:** unit test
      `test_calibration_fixture_b_zero_keeps_three_drops`.
- [ ] **H3.** Fixture C (mixed): stub returns 1 valid memory + 3 valid
      rejections. `len(extract_memories(...)) == 1`; exactly THREE
      `daydream.candidate_rejected` events fire (all before the single
      `daydream.chunk_extracted`); ZERO `chunk_partial_parse` events fire.
      **Verify:** unit test
      `test_calibration_fixture_c_mixed_one_keep_three_drops`.
- [ ] **H4.** Fixture C's three rejection events have `batch_index` set
      equal to `{0, 1, 2}` (no duplicates, no off-by-one). **Verify:**
      unit test
      `test_calibration_fixture_c_rejection_batch_indices_are_zero_one_two`.
- [ ] **H5.** Fixture C's three rejection events' `rationale` values match
      the canned input rationales byte-equal (no transformation other than
      the 200-char cap). **Verify:** unit test
      `test_calibration_fixture_c_rationale_values_pass_through`.
- [ ] **H6.** Fixture D (backward-compat): stub returns
      `{"memories":[{"content":"x"}]}` (no `rejected` key).
      `len(extract_memories(...)) == 1`; ZERO rejection events; ZERO
      `chunk_partial_parse` events; ZERO `chunk_skipped_parse_failed`
      events. **Verify:** unit test
      `test_calibration_fixture_d_backward_compat_no_rejected_key`.
- [ ] **H7.** All calibration fixtures use a stub `LLMClient` (NOT a real
      provider). The test source contains NO import of `OpenRouterClient`,
      `EchoClient`, or any `make_client` factory at the §SELECTIVE banner's
      test scope. **Verify:** unit test
      `test_calibration_fixtures_use_stub_client_only` — AST walk of the
      `# §SELECTIVE` banner test region; assert no import sites pulling in
      provider clients.
- [ ] **H8.** A `_ok_completion_with_rejections(memories, rejected)` helper
      exists in the test module and returns a `Completion` whose `text`
      is `json.dumps({"memories": memories, "rejected": rejected})` and
      whose `tokens_in`/`tokens_out` are non-negative integers. **Verify:**
      unit test `test_ok_completion_with_rejections_helper_shape`.

## I. Imports + non-coupling — `_extract.py` import allow-list + event name

- [ ] **I1.** `_extract.py` module-top imports are EXACTLY the set
      `{"hashlib", "json", "logging", "uuid", "typing.Any", "typing.Callable",
      "memeval.cost.cost_of", "memeval.dreaming.events.emit",
      "memeval.dreaming.llm.Completion", "memeval.dreaming.llm.LLMClient",
      "memeval.dreaming.prompts.EXTRACTION_SYSTEM_PROMPT",
      "memeval.dreaming.prompts._ENVELOPE_TEMPLATE",
      "memeval.dreaming.redaction.RedactedText",
      "memeval.dreaming.redaction.redact",
      "memeval.schema.MemoryItem"}` (set-equal — `redact` ADDED per
      halliday B1 second-pass redaction at the emit seam).
      **Verify:** unit test
      `test_extract_module_top_imports_unchanged` — AST parse, collect
      `Import`/`ImportFrom` first-level nodes, assert set equality.
- [ ] **I2.** `_extract.py` module-top contains NO third-party import (no
      `httpx`, `openai`, `anthropic`, `voyage`, `numpy`, `pydantic`).
      **Verify:** shell command
      `! grep -nE '^(import|from)[[:space:]]+(httpx|openai|anthropic|voyage|numpy|pydantic)\\b' eval/memeval/dreaming/_extract.py`.
- [ ] **I3.** The string literal `"daydream.candidate_rejected"` appears
      EXACTLY ONCE in `_extract.py`. **Verify:** shell command
      `test "$(grep -c '"daydream.candidate_rejected"' eval/memeval/dreaming/_extract.py)" -eq 1`.
- [ ] **I3b.** The string literal `"daydream.rejected_field_missing"`
      appears EXACTLY ONCE in `_extract.py` (halliday B3). **Verify:**
      shell command
      `test "$(grep -c '"daydream.rejected_field_missing"' eval/memeval/dreaming/_extract.py)" -eq 1`.
- [ ] **I4.** The new constants `_REJECTION_SNIPPET_MAX_LEN` and
      `_REJECTION_RATIONALE_MAX_LEN` are referenced from `_extract.py`
      (not redefined inline). **Verify:** unit test
      `test_rejection_caps_used_via_module_constants_not_inline_literals`.
- [ ] **I5.** `_extract.py` source contains NO direct `RedactedText(...)`
      wrap of any rejection-event kwarg (events are local-only per ADR-009;
      redaction boundary is at the LLM-call seam, not the emit seam).
      **Verify:** unit test
      `test_rejection_event_kwargs_are_not_redactedtext_wrapped` — AST walk
      of the rejection-emit site; assert kwargs are plain `Name`/`Subscript`
      nodes, not `Call(func=Name(id="RedactedText"))`.
- [ ] **I6.** `_extract.py` source contains exactly ONE call site for
      `_wrap_user_content_in_envelope` (no new wrapper site; the 3-wrapper
      AST audit at `test_extract.py:679-720` continues to pass UNMODIFIED).
      **Verify:** unit test
      `test_extract_envelope_wrapper_call_site_count_unchanged_at_one`.

## J. Non-goals — pinned exclusions

- [ ] **J1.** No new ADR file is added under `docs/adrs/` by this PR.
      **Verify:** shell command
      `test -z "$(git diff --name-only origin/main -- docs/adrs/ | grep -v '^docs/adrs/README.md$' || true)"` (only the README index is allowed to change; no new ADR file).
- [ ] **J2.** No edit to `eval/memeval/dreaming/worker.py` (Job 3 governance
      is untouched by this PR). **Verify:** shell command
      `test -z "$(git diff --name-only origin/main -- eval/memeval/dreaming/worker.py)"`.
- [ ] **J3.** No edit to `eval/memeval/dreaming/prompts.py` outside the
      `EXTRACTION_SYSTEM_PROMPT` constant body. `_ENVELOPE_TEMPLATE`,
      `CONTRADICTION_SYSTEM_PROMPT`, and `GOVERNANCE_SYSTEM_PROMPT` (if
      present) are byte-equal pre/post. **Verify:** unit test
      `test_prompts_py_only_extraction_constant_changed` — sha256 of
      `_ENVELOPE_TEMPLATE` and any non-extraction prompt constants holds.
- [ ] **J4.** No `RedactedText(...)` wrap of `content_snippet`, `rationale`,
      `session_id`, or `batch_index` at the `daydream.candidate_rejected`
      emit site. **Verify:** covered by §I5.
- [ ] **J5.** No new env var. `_extract.py` source contains no new
      `os.environ`/`os.getenv` reference compared to pre-PR. **Verify:**
      shell command
      `test "$(grep -cE 'os\\.(environ|getenv)' eval/memeval/dreaming/_extract.py)" -eq "$(git show origin/main:eval/memeval/dreaming/_extract.py | grep -cE 'os\\.(environ|getenv)')"`.
- [ ] **J6.** No new `daydream.*` event NAME other than
      `daydream.candidate_rejected`. **Verify:** covered by §E1 +
      §E10.
- [ ] **J7.** No change to the Job 3 governance prompt
      (`GOVERNANCE_SYSTEM_PROMPT` if present), no change to its sha256
      pin, no change to Job 3 event-allow-set. **Verify:** shell command
      `test -z "$(git diff --name-only origin/main -- eval/memeval/dreaming/tests/test_worker_governance.py)"`.
- [ ] **J8.** Bench framing in the PR description is "may improve, may not"
      — NOT a guaranteed positive delta. **Verify:** human-judgment review
      of PR description (rubric grader: flag as `JUDGMENT REQUIRED` if the
      description claims a measured positive bench delta without a
      reproducible benchmark run committed).
- [ ] **J9.** No top-level third-party import added to `_extract.py`
      (preserved). **Verify:** covered by §I2.
- [ ] **J10.** No new wrapper function or new envelope-template variant.
      The 3-wrapper AST audit at `test_extract.py:679-720` passes
      UNMODIFIED on this PR's working tree. **Verify:** shell command
      `pytest eval/memeval/dreaming/tests/test_extract.py -k envelope_wrapper -q`
      (the existing audit test name; if the implementer added a fourth
      wrapper, this test fails — and the PR FAILS this rubric).
- [ ] **J11.** The PR description contains four pinned statements per the
      preamble's halliday A7 checklist: (i) threshold is MODERATE; (ii)
      operator test is "would a future session benefit from this fact?";
      (iii) rejection-event diary is the calibration surface; (iv) no
      Job 3 governance changes are bundled. **Verify:** human-judgment
      review of PR description (rubric grader: flag as FAIL if any of
      the four pinned statements is missing or substantively softened).

---

## rubric_adversarial_pass

This rubric was authored from the dispatcher's plan + the existing
`_extract.py` / `prompts.py` source. Two adversarial questions, answered
explicitly:

### Question 1 — What does this rubric miss?

**Identified gaps (RUBRIC_GAP items — do not block grading on the current
rubric but are formally surfaced):**

- **RUBRIC_GAP-1.** No criterion asserts the rejection-parse loop is
  EXACTLY positioned BETWEEN the `memories` shape validation (current
  `_extract.py:135`) and the `memories` build loop (current line 137). A
  pathological implementation could put the rejection emit AFTER the
  `daydream.chunk_extracted` emit, violating §E8's ordering pin only
  indirectly. §E8 catches the symptom; no criterion catches the
  structural placement. A future incremental review should add a
  source-position assertion or an AST walk pinning the relative line
  ordering of the rejection loop versus the chunk-extracted emit.
- **RUBRIC_GAP-2.** No criterion asserts the `rejected_n_kept` count in
  the `chunk_partial_parse` event equals the number of
  `daydream.candidate_rejected` events that fired in the same call. The
  two counts are derived from the same loop but the rubric grades them
  independently (§D4 + §H4); a bug that double-counts in one place but
  not the other would slip through. Add a cross-check criterion in a
  follow-up: `count(daydream.candidate_rejected events) ==
  chunk_partial_parse.kwargs["rejected_n_kept"]` when the partial-parse
  event fires.
- **RUBRIC_GAP-3.** No criterion exercises the case where the LLM emits
  an `id` or `session_id` field INSIDE a rejection row (an
  injection-attempt where the model tries to influence the engine-side
  `session_id`). §E5 asserts the engine override but does NOT directly
  test the "LLM tries to supply `session_id`" attack surface. Add a
  fixture row `{"content_snippet":"x","rationale":"y","session_id":"attacker_sid"}`
  and assert the emitted event's `session_id` is still the
  caller-supplied value.
- **RUBRIC_GAP-4.** No criterion verifies the SOURCE of the prompt body
  preserves the DATA/nonce injection-defense block VERBATIM. §C-SUBSTRING-9
  and §C-SUBSTRING-10 pin two substrings from that block, but a wholesale
  rewrite that keeps those substrings while changing the surrounding
  semantics would pass. A diff-based assertion (lines 39-51 of pre-PR
  preserved byte-equal) would close this gap. Trade-off: that rule
  becomes brittle to legitimate refactors of the injection block; the
  substring pins are the loose-but-grep-friendly version.
- **RUBRIC_GAP-5.** No criterion verifies that the rejection parse loop
  is wrapped in the same JSON-parse exception-handling boundary as the
  memories build loop. A malformed JSON inside `rejected` should already
  be unreachable (it's inside the top-level JSON parse), but if the
  implementer added a second `json.loads` call inside the rejection
  loop (e.g. for a nested field), that would re-introduce the
  return-`None`-vs-partial-parse confusion. §E1's allow-set bounds the
  symptom but not the structure.

### Question 2 — Where is this rubric aligned to the dispatcher's framing rather than to the artifact's truth conditions?

**Identified alignment risks:**

- **ALIGNMENT-1.** The rubric was authored from the dispatcher's PLAN
  rather than from a reviewed-and-frozen ADR. The plan §2 dispatcher
  scope-calls are accepted as "open contracts pinned" in the preamble.
  This is the standard posture for a single-PR refinement rubric (the
  same posture JOB2 and JOB3 rubrics take), but it means a future
  reviewer asking "why MODERATE selectivity?" sees the answer in the
  rubric preamble + PR description, not in an ADR. If the threshold
  proves wrong post-bench, the path to revise is "amend rubric +
  re-grade," not "supersede ADR."
- **ALIGNMENT-2.** The substring contract (§C-SUBSTRING) is the contract,
  not the prompt body. This is a deliberate framing choice from plan
  §7.3: the prompt MAY evolve across model versions for calibration,
  and the substring set holds. The risk is that a degenerate prompt
  containing all twelve required substrings as disconnected tokens
  (e.g., "[the words `durable`, `decisions`, `commitments`, ... appear
  here for compliance]") would pass §C-SUBSTRING while not actually
  enforcing the selectivity threshold. §C-SCHEMA (the surrounding-text
  proximity checks for "required" near key names; the cap-documentation
  checks) tightens this partially, but a sufficiently adversarial
  prompt body could still pass §C without enforcing intent. The
  fundamental answer: prompt-body QUALITY is the LLM-as-judge problem
  this rubric DELIBERATELY does not attempt to test (§H7 pins stubs,
  not real LLM). A future "prompt-quality eval" PR would close this gap
  via paired-comparison judgments — outside this rubric's scope.
- **ALIGNMENT-3.** The bench framing (§J8) is human-judgment-required
  rather than mechanical. The rubric author had no way to verify the
  PR description at rubric-writing time. The grader is asked to flag if
  the PR claims a measured bench delta without a reproducible benchmark
  run — this is the closest mechanical proxy for the dispatcher's
  "honest framing" requirement (plan §2 dispatcher #10), but it is
  judgment-based, not boolean. A more rigorous version would require
  a `bench-results.txt` artifact + a `bench-replay.sh` script — out of
  scope for a prompt-and-parser refinement PR.
- **ALIGNMENT-4.** The "no Job 3 governance changes" pin (§J2) is
  enforced by a `git diff --name-only origin/main -- worker.py`
  emptiness check. This catches surface-level edits but does NOT catch
  the case where a separate PR landing in parallel introduces a
  Daydream/Job-3 coupling that this PR's branch then inherits. The
  CODEOWNERS layout makes that unlikely, but the rubric does not
  GUARANTEE it.

**Conclusion.** The rubric grades structural fidelity to the plan +
preserves the existing architectural envelope (ADR-013 cursor-non-advance,
ADR-010 redaction boundary, ADR-009 events-shim) by pinning observable
properties. It deliberately does NOT grade prompt-body quality or bench
delta. The RUBRIC_GAP items above are surfaced for the dispatcher's
awareness; they do not block this PR's grading on the criteria below.

---

## Coverage-gate self-check (3-check)

Before any final grade is issued on this rubric, the grader MUST run
the three checks below and report the output. A non-empty result on
any of (1) or (3), or a sha mismatch on (2), is a BLOCKER on
grading — not a per-criterion FAIL.

### Check 1 — `comm -23` on `§DAYDREAM-*` tags (every rubric tag has a test docstring tag)

Every test docstring under the `# §SELECTIVE` banner in
`tests/test_extract.py` MUST contain a `§DAYDREAM-<id>` tag matching
a rubric tag in this file. The rubric uses §A–§J section letters and
sub-letters as the tag identifiers; the implementer adds matching tags
in test docstrings.

```bash
grep -oE '§DAYDREAM-[A-Z0-9-]+' eval/memeval/dreaming/tests/DAYDREAM_SELECTIVE_RUBRIC.md \
  | sort -u > /tmp/rubric_ids.txt
grep -roE '§DAYDREAM-[A-Z0-9-]+' eval/memeval/dreaming/tests/ \
  | grep -oE '§DAYDREAM-[A-Z0-9-]+' \
  | sort -u > /tmp/test_ids.txt
comm -23 /tmp/rubric_ids.txt /tmp/test_ids.txt
```

A non-empty result names rubric tags with no corresponding test. The
absence of `§DAYDREAM-*` tags in this rubric file (it uses §A1/§B1
section-letter tags instead) means the grader applies this check at
implementation time after the implementer adopts a `§DAYDREAM-<section>-<id>`
docstring convention. If the implementer chose a different docstring
convention, the grader substitutes that pattern in the grep and
re-runs.

### Check 2 — sha256 verification (live recompute matches both literals)

The `_SYSTEM_PROMPT_SHA256` literal at `test_extract.py:43` and the
mirror at `test_prompts.py:89` MUST be byte-equal AND MUST match the
live sha256 of `EXTRACTION_SYSTEM_PROMPT`.

```bash
cd /Users/nerd/Git/agent-memory-harness/eval
LIVE=$(python3 -c "import hashlib; from memeval.dreaming.prompts import EXTRACTION_SYSTEM_PROMPT; print(hashlib.sha256(EXTRACTION_SYSTEM_PROMPT.encode('utf-8')).hexdigest())")
PIN_EXTRACT=$(python3 -c "import re,pathlib; print(re.search(r'_SYSTEM_PROMPT_SHA256\s*=\s*\"([0-9a-f]{64})\"', pathlib.Path('memeval/dreaming/tests/test_extract.py').read_text()).group(1))")
PIN_PROMPTS=$(python3 -c "import re,pathlib; print(re.search(r'\"([0-9a-f]{64})\"', pathlib.Path('memeval/dreaming/tests/test_prompts.py').read_text()).group(1))")
echo "LIVE:     $LIVE"
echo "EXTRACT:  $PIN_EXTRACT"
echo "PROMPTS:  $PIN_PROMPTS"
test "$LIVE" = "$PIN_EXTRACT" -a "$LIVE" = "$PIN_PROMPTS" && echo OK || echo MISMATCH
```

A `MISMATCH` is a BLOCKER. The pins MUST be rotated together.

### Check 3 — Event-allow-set AST audit (no rogue event names in `_extract.py`)

The full set of `emit("...")` first-arg string literals in
`_extract.py` MUST equal the five-name set pinned by §E1. This is the
mechanical version of §E1's unit test, re-run by the grader as a
gate-check independent of the test suite.

```bash
python3 -c "
import ast, pathlib
src = pathlib.Path('eval/memeval/dreaming/_extract.py').read_text(encoding='utf-8')
tree = ast.parse(src)
found = set()
dynamic = []
for node in ast.walk(tree):
    if (isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == 'emit'
        and node.args):
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            found.add(arg.value)
        else:
            dynamic.append(ast.dump(arg))
EXPECTED = {
    'chunk_skipped_unavailable_llm',
    'chunk_skipped_parse_failed',
    'chunk_partial_parse',
    'daydream.chunk_extracted',
    'daydream.candidate_rejected',
    'daydream.rejected_field_missing',
}
assert not dynamic, f'dynamic event names: {dynamic}'
assert found == EXPECTED, f'drift: missing={EXPECTED - found} extra={found - EXPECTED}'
print('OK')
"
```

A non-`OK` exit is a BLOCKER. Drift in either direction (missing or
extra event names) signals an event-allow-set violation that the §E
criteria's per-test verification might miss if a test file is excluded
from the run.

---

**End of rubric.**
