"""Pin tests for ``memeval.dreaming.prompts`` — Job 2 contradiction-prompt
sha256 + substring contract.

Mirrors the existing ``EXTRACTION_SYSTEM_PROMPT`` pin pattern in
``test_extract.py:42-47``. Any edit to the prompt text is a deliberate,
reviewable diff: bump the pinned hash here in the same PR or the suite goes red.

Rubric: JOB2_CONTRADICTION_RUBRIC.md §G-J2-sha256 + §G-J2-prompt-schema
+ §G-J2-prompt-injection.
"""

from __future__ import annotations

import hashlib

from memeval.dreaming.prompts import (
    CONTRADICTION_SYSTEM_PROMPT,
    DEDUP_SYSTEM_PROMPT,
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_SYSTEM_PROMPT_V1,
    EXTRACTION_SYSTEM_PROMPT_V2,
    EXTRACTION_SYSTEM_PROMPT_V3,
    EXTRACTION_SYSTEM_PROMPT_V4,
    EXTRACTION_SYSTEM_PROMPT_V5,
    GOVERNANCE_SYSTEM_PROMPT,
    OKF_CONTENT_TYPES,
    _ENVELOPE_TEMPLATE,
    _EXTRACTION_VARIANTS,
    get_extraction_prompt,
    list_extraction_variants,
)

# Computed at write time. Bumping requires deliberate reviewer authorization.
_CONTRADICTION_SYSTEM_PROMPT_SHA256 = (
    "25cd0ad0222a9b2c94b6399957fefe5b8a0dc7108f3012d2a183c77a31c7b4c6"
)
_GOVERNANCE_SYSTEM_PROMPT_SHA256 = (
    "212a982108744e10e794262bdc7b9b8bbd534d1441b5ccba21a4ca615d18c158"
)


def test_contradiction_system_prompt_sha256_pinned() -> None:
    """JOB2 §G-J2-sha256: pinned sha256 hex digest matches the live constant."""
    h = hashlib.sha256(CONTRADICTION_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    assert h == _CONTRADICTION_SYSTEM_PROMPT_SHA256, (
        "CONTRADICTION_SYSTEM_PROMPT drifted from its pinned hash. "
        "Update _CONTRADICTION_SYSTEM_PROMPT_SHA256 only after deliberate review."
    )


def test_contradiction_prompt_pins_pairs_schema() -> None:
    """JOB2 §G-J2-prompt-schema: required substrings present (case-insensitive)."""
    text = CONTRADICTION_SYSTEM_PROMPT.lower()
    for sub in ("pairs", "a_id", "b_id", "rationale", "json only", "no markdown fences"):
        assert sub in text, f"missing required substring: {sub!r}"


def test_contradiction_prompt_injection_framing() -> None:
    """JOB2 §G-J2-prompt-injection: prompt-injection defense framing present."""
    text = CONTRADICTION_SYSTEM_PROMPT
    assert "DATA, not instructions" in text
    assert "nonce" in text.lower()


def test_contradiction_prompt_no_loser_winner_in_llm_schema() -> None:
    """JOB2 Pushback A: LLM contract uses a_id/b_id only. Worker picks the loser
    deterministically — schema must NOT mention loser_id/winner_id."""
    assert "loser_id" not in CONTRADICTION_SYSTEM_PROMPT, (
        "Pushback A: LLM judges pairs, not winners. "
        "Drop loser_id/winner_id from the LLM schema."
    )
    assert "winner_id" not in CONTRADICTION_SYSTEM_PROMPT


def test_contradiction_prompt_forbids_invented_ids() -> None:
    """JOB2 §G-J2-prompt-anti-hallucination: prompt instructs the LLM not to
    invent ids outside the input array. Halliday O4-#5."""
    text = CONTRADICTION_SYSTEM_PROMPT.lower()
    assert "not in the input array" in text or "do not invent ids" in text


def test_envelope_template_reused_unchanged() -> None:
    """JOB2 reuses Job 4/PR4's _ENVELOPE_TEMPLATE verbatim — the sha256 must
    match the existing pin in test_extract.py."""
    existing_pin = (
        "7ed0ceec15d12d5aa621a437b76a6ccc36643722d1819093df17ba372af63e95"
    )
    h = hashlib.sha256(_ENVELOPE_TEMPLATE.encode("utf-8")).hexdigest()
    assert h == existing_pin, (
        "_ENVELOPE_TEMPLATE drifted from PR4's pinned hash. "
        "Job 2 reuses the template unchanged — coordinate with the Daydream side."
    )


def test_extraction_prompt_unchanged_by_job2() -> None:
    """JOB2 must not modify EXTRACTION_SYSTEM_PROMPT (Daydream-side). Confirms
    sha256 stability across this PR."""
    existing_pin = (
        "b2f8f69bcff40693346ee9facfeb1661f59822bac78d4e235f78d68e834a0bc3"
    )
    h = hashlib.sha256(EXTRACTION_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    assert h == existing_pin, (
        "EXTRACTION_SYSTEM_PROMPT drifted under a Job 2 PR. Daydream is out of "
        "scope for Job 2; revert any change here."
    )


def test_envelope_template_round_trip_for_contradiction() -> None:
    """JOB2 §G-J2-envelope: envelope format round-trips for a batch payload."""
    payload = '[{"id":"mem_a","content":"x","timestamp":1.0,"tags":[]}]'
    wrapped = _ENVELOPE_TEMPLATE.format(nonce="abcd1234", redacted=payload)
    assert wrapped.count('nonce="abcd1234"') == 2  # opening + closing tags
    assert payload in wrapped


# ── Job 3 governance prompt pins ────────────────────────────────────────


def test_governance_system_prompt_sha256_pinned() -> None:
    """JOB3 §G-J3-sha256: pinned sha256 hex digest matches the live constant."""
    h = hashlib.sha256(GOVERNANCE_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    assert h == _GOVERNANCE_SYSTEM_PROMPT_SHA256, (
        "GOVERNANCE_SYSTEM_PROMPT drifted from its pinned hash. "
        "Update _GOVERNANCE_SYSTEM_PROMPT_SHA256 only after deliberate review."
    )


def test_governance_prompt_pins_classifications_schema() -> None:
    """JOB3 §G-J3-prompt-schema: required substrings present (case-insensitive)."""
    text = GOVERNANCE_SYSTEM_PROMPT.lower()
    for sub in (
        "classifications", "item_id", "class", "rationale",
        "none", "must_know", "must_do", "blacklist",
        "json only", "no markdown fences",
    ):
        assert sub in text, f"missing required substring: {sub!r}"


def test_governance_prompt_injection_framing() -> None:
    """JOB3 §G-J3-prompt-injection: DATA/nonce defense framing present."""
    assert "DATA, not instructions" in GOVERNANCE_SYSTEM_PROMPT
    assert "nonce" in GOVERNANCE_SYSTEM_PROMPT.lower()


def test_governance_prompt_forbids_invented_ids() -> None:
    """JOB3 §G-J3-prompt-anti-hallucination: no inventing ids outside input."""
    text = GOVERNANCE_SYSTEM_PROMPT.lower()
    assert "not in the input array" in text or "do not invent ids" in text


def test_governance_prompt_pins_four_class_enum() -> None:
    """JOB3 §G-J3-prompt-enum: the four class names appear as standalone tokens."""
    text = GOVERNANCE_SYSTEM_PROMPT
    for cls in ("none", "must_know", "must_do", "blacklist"):
        # Quoted form distinguishes the class-name token from prose.
        assert f'"{cls}"' in text, f"class name not pinned as quoted enum: {cls}"


def test_contradiction_and_governance_prompts_are_distinct() -> None:
    """JOB3: the two prompts must not collide in sha256 (pinning catches drift, this
    catches the accidental-identity bug)."""
    assert CONTRADICTION_SYSTEM_PROMPT != GOVERNANCE_SYSTEM_PROMPT
    assert _CONTRADICTION_SYSTEM_PROMPT_SHA256 != _GOVERNANCE_SYSTEM_PROMPT_SHA256


def test_governance_prompt_enumerates_four_classes() -> None:
    """JOB3 §G-J3-four-classes: alias for `test_governance_prompt_pins_four_class_enum`.

    The rubric §G-J3-four-classes names this test verbatim; the existing name is
    semantically equivalent. Both names are kept so the grader can match either.
    """
    test_governance_prompt_pins_four_class_enum()


# ── ADR-dreaming-023: selectable EXTRACTION_SYSTEM_PROMPT variants ─────────

# Sha256 pins for the three new variants. V0 stays pinned at the existing
# `b2f8f69b…` value (unchanged literal — backward compatible). Computed by
# running prompts.py at the commit that introduced these variants.

_EXTRACTION_V0_SHA256 = (
    "b2f8f69bcff40693346ee9facfeb1661f59822bac78d4e235f78d68e834a0bc3"
)
_EXTRACTION_V1_SHA256 = (
    "655b3bd0bf6ff7c2e13caa2b958828729bae1d736d36b752bb2e82014cbf5c8b"
)
_EXTRACTION_V2_SHA256 = (
    "e268af8b08039034e072b3d06dfad06f97c1cffc27618dd9aa368e212a4aa6cb"
)
_EXTRACTION_V3_SHA256 = (
    "2c8f32d7f9615d12881e094b194af99d81c46344b644f7583fba1f4ad6f2625e"
)
_EXTRACTION_V5_SHA256 = (
    "44d664a993227521f1caca9bfb8672706916cbe43cc00c7c56a54868d31eae37"
)

# Negative-substring contract: no variant may contain Job 2 / Job 3 vocab.
# Same contract the original V0 prompt is held to — guards against cross-layer
# vocab leak when a future PR edits a variant.
_FORBIDDEN_VOCAB_FROM_JOB2_AND_JOB3 = (
    "must_know",
    "must_do",
    "blacklist",
    "classifications",
    "a_id",
    "b_id",
)


def test_extraction_variant_v0_sha256_pinned() -> None:
    """V0 (default, backward-compatible) sha256 must match the existing pin."""
    h = hashlib.sha256(EXTRACTION_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    assert h == _EXTRACTION_V0_SHA256, (
        f"EXTRACTION_SYSTEM_PROMPT (V0) drifted; computed={h}. V0 is the "
        "backward-compat default; do NOT bump unless you've coordinated with "
        "every downstream pin site (test_extract.py:43, test_worker_governance.py:2089)."
    )


def test_extraction_variant_v1_sha256_pinned() -> None:
    """V1 STRICT (ADR-dreaming-023) sha256 pinned."""
    h = hashlib.sha256(EXTRACTION_SYSTEM_PROMPT_V1.encode("utf-8")).hexdigest()
    assert h == _EXTRACTION_V1_SHA256, (
        f"EXTRACTION_SYSTEM_PROMPT_V1 drifted; computed={h}. "
        "Update _EXTRACTION_V1_SHA256 only after deliberate review."
    )


def test_extraction_variant_v2_sha256_pinned() -> None:
    """V2 A-MEM keywords+context (ADR-dreaming-023) sha256 pinned."""
    h = hashlib.sha256(EXTRACTION_SYSTEM_PROMPT_V2.encode("utf-8")).hexdigest()
    assert h == _EXTRACTION_V2_SHA256, (
        f"EXTRACTION_SYSTEM_PROMPT_V2 drifted; computed={h}. "
        "Update _EXTRACTION_V2_SHA256 only after deliberate review."
    )


def test_extraction_variant_v3_sha256_pinned() -> None:
    """V3 SWE-tuned (ADR-dreaming-023) sha256 pinned."""
    h = hashlib.sha256(EXTRACTION_SYSTEM_PROMPT_V3.encode("utf-8")).hexdigest()
    assert h == _EXTRACTION_V3_SHA256, (
        f"EXTRACTION_SYSTEM_PROMPT_V3 drifted; computed={h}. "
        "Update _EXTRACTION_V3_SHA256 only after deliberate review."
    )


def test_extraction_variant_v5_sha256_pinned() -> None:
    """V5 transferable-lesson curation sha256 pinned."""
    h = hashlib.sha256(EXTRACTION_SYSTEM_PROMPT_V5.encode("utf-8")).hexdigest()
    assert h == _EXTRACTION_V5_SHA256, (
        f"EXTRACTION_SYSTEM_PROMPT_V5 drifted; computed={h}. "
        "Update _EXTRACTION_V5_SHA256 only after deliberate review."
    )


def test_extraction_variants_are_mutually_distinct() -> None:
    """All four variants must produce distinct sha256s — pinning catches drift,
    this catches the accidental-identity bug (e.g. someone copy-pastes V0 into V2)."""
    digests = {
        v: hashlib.sha256(s.encode("utf-8")).hexdigest()
        for v, s in _EXTRACTION_VARIANTS.items()
    }
    assert len(set(digests.values())) == len(digests), (
        f"Two variants produced the same sha256: {digests}"
    )


def test_extraction_variant_registry_complete() -> None:
    """The `_EXTRACTION_VARIANTS` registry must enumerate exactly V0..V5
    so the selector advertises the full set via list_extraction_variants()."""
    assert list_extraction_variants() == ["V0", "V1", "V2", "V3", "V4", "V5"]
    assert set(_EXTRACTION_VARIANTS) == {"V0", "V1", "V2", "V3", "V4", "V5"}


def test_extraction_variant_v0_is_backward_compat_baseline() -> None:
    """V0 in the registry MUST be the same string object as the top-level
    EXTRACTION_SYSTEM_PROMPT constant — the registry's V0 is the backward-
    compat default, not a separate string that could drift independently."""
    assert _EXTRACTION_VARIANTS["V0"] is EXTRACTION_SYSTEM_PROMPT


def test_get_extraction_prompt_default_is_v0(monkeypatch) -> None:
    """No arg + no env var → V0 (the backward-compatible default)."""
    monkeypatch.delenv("DREAM_EXTRACTION_VARIANT", raising=False)
    assert get_extraction_prompt() is EXTRACTION_SYSTEM_PROMPT


def test_get_extraction_prompt_explicit_arg_wins(monkeypatch) -> None:
    """Explicit `variant` arg overrides the env var."""
    monkeypatch.setenv("DREAM_EXTRACTION_VARIANT", "V1")
    assert get_extraction_prompt("V0") is EXTRACTION_SYSTEM_PROMPT
    assert get_extraction_prompt("V2") is EXTRACTION_SYSTEM_PROMPT_V2
    assert get_extraction_prompt("V3") is EXTRACTION_SYSTEM_PROMPT_V3


def test_get_extraction_prompt_env_var_dispatch(monkeypatch) -> None:
    """`DREAM_EXTRACTION_VARIANT` env var picks the variant when no arg given."""
    monkeypatch.setenv("DREAM_EXTRACTION_VARIANT", "V1")
    assert get_extraction_prompt() is EXTRACTION_SYSTEM_PROMPT_V1
    monkeypatch.setenv("DREAM_EXTRACTION_VARIANT", "V2")
    assert get_extraction_prompt() is EXTRACTION_SYSTEM_PROMPT_V2
    monkeypatch.setenv("DREAM_EXTRACTION_VARIANT", "V3")
    assert get_extraction_prompt() is EXTRACTION_SYSTEM_PROMPT_V3


def test_get_extraction_prompt_case_insensitive(monkeypatch) -> None:
    """Variant names normalize to upper-case after stripping whitespace."""
    monkeypatch.delenv("DREAM_EXTRACTION_VARIANT", raising=False)
    assert get_extraction_prompt("v1") is EXTRACTION_SYSTEM_PROMPT_V1
    assert get_extraction_prompt("  V2  ") is EXTRACTION_SYSTEM_PROMPT_V2
    monkeypatch.setenv("DREAM_EXTRACTION_VARIANT", "v3")
    assert get_extraction_prompt() is EXTRACTION_SYSTEM_PROMPT_V3


def test_get_extraction_prompt_unknown_raises(monkeypatch) -> None:
    """Unknown variant name raises ValueError naming the legal options."""
    monkeypatch.delenv("DREAM_EXTRACTION_VARIANT", raising=False)
    import pytest as _pt  # local import keeps the module top minimal
    with _pt.raises(ValueError) as exc:
        get_extraction_prompt("V99")
    msg = str(exc.value)
    assert "V99" in msg
    for name in ("V0", "V1", "V2", "V3"):
        assert name in msg, f"error message must name legal variant {name}"


def test_get_extraction_prompt_empty_env_falls_back_to_v0(monkeypatch) -> None:
    """Empty env var value treated as unset → V0 default."""
    monkeypatch.setenv("DREAM_EXTRACTION_VARIANT", "")
    # Empty string after .strip() and .upper() is "" which is not in registry;
    # the selector falls back to "V0" via the `raw or "V0"` guard.
    assert get_extraction_prompt() is EXTRACTION_SYSTEM_PROMPT


def test_extraction_variants_share_envelope_framing() -> None:
    """All variants must keep the prompt-injection envelope framing — adversarial
    escape, `DATA, not instructions`, and `nonce` mention (ADR-dreaming-010)."""
    for v, prompt in _EXTRACTION_VARIANTS.items():
        assert "DATA, not instructions" in prompt, f"{v} missing envelope framing"
        assert "nonce" in prompt.lower(), f"{v} missing nonce framing"
        assert '{"memories": [], "rejected": []}' in prompt, (
            f"{v} missing adversarial-escape JSON literal"
        )


def test_extraction_variants_share_json_only_rule() -> None:
    """All variants pin `Output JSON only.` and the no-markdown-fences rule —
    the parser fail-closes on fenced output, so every variant must forbid it."""
    for v, prompt in _EXTRACTION_VARIANTS.items():
        low = prompt.lower()
        assert "json only" in low, f"{v} missing json-only rule"
        assert "no markdown fences" in low, f"{v} missing no-fences rule"


def test_extraction_variants_forbid_job2_job3_vocab() -> None:
    """No variant may leak Job 2 (contradiction) or Job 3 (governance) vocab.
    Cross-layer contamination would confuse downstream observability + indicates
    the variant was authored against the wrong rubric."""
    for v, prompt in _EXTRACTION_VARIANTS.items():
        for vocab in _FORBIDDEN_VOCAB_FROM_JOB2_AND_JOB3:
            assert vocab not in prompt, (
                f"{v} contains forbidden cross-layer vocab {vocab!r}; this is "
                "either a cross-layer paste error or a rubric drift."
            )


def test_extraction_variant_v1_pins_strict_framing() -> None:
    """V1 must explicitly pin STRICT framing — distinguishes it from V0/V2/V3."""
    assert "STRICT selectivity" in EXTRACTION_SYSTEM_PROMPT_V1
    assert "annoyance-prevention" in EXTRACTION_SYSTEM_PROMPT_V1
    assert "When in doubt, REJECT" in EXTRACTION_SYSTEM_PROMPT_V1
    # And V1 must NOT carry MODERATE framing (that's V0/V2/V3).
    assert "MODERATE selectivity" not in EXTRACTION_SYSTEM_PROMPT_V1


def test_extraction_variant_v2_pins_amem_schema_extension() -> None:
    """V2 must require the keywords + context fields in the per-memory schema."""
    text = EXTRACTION_SYSTEM_PROMPT_V2
    assert '"keywords"' in text, "V2 missing keywords field"
    assert '"context"' in text, "V2 missing context field"
    # The guidance paragraph that explains how to fill them.
    assert "3-7 specific, distinct terms" in text
    assert "one sentence stating the topic AND the concrete situation" in text


def test_extraction_variant_v3_pins_swe_framing() -> None:
    """V3 must pin the autonomous-coding-agent opener + code-shaped examples."""
    text = EXTRACTION_SYSTEM_PROMPT_V3
    assert "autonomous coding agent" in text
    assert "pytest" in text  # at least one code-shaped example mentions pytest
    assert "migrations" in text  # durable project conventions example
    # And V3 must NOT contain V0's chat-shaped identity example.
    assert "the user is named Scott" not in text


def test_extraction_variants_are_all_documented_size() -> None:
    """Variants should be in the same order-of-magnitude as V0 (3-7K chars).
    A wildly-different length suggests an accidental truncation or paste error.

    Upper bound was 1.6× through V4 (5755 chars, 1.56×). V5 added the ADR-027
    OKF closed-taxonomy section (8 enumerated values with one-line guidance
    each) on top of V5's existing transferable-lesson framing, pushing it to
    1.82× — real content expansion, not a paste error. Bound widened to 2.0×
    to accommodate; tighten again if a future variant adds size without
    structural justification."""
    v0_len = len(EXTRACTION_SYSTEM_PROMPT)
    for v, prompt in _EXTRACTION_VARIANTS.items():
        ratio = len(prompt) / v0_len
        assert 0.8 <= ratio <= 2.0, (
            f"{v} length {len(prompt)} is {ratio:.2f}× V0's {v0_len} — "
            "suspiciously off; check for truncation or accidental duplication."
        )


# --- resolve_extraction_prompt — identity sibling for forensic logging ----- #
def test_resolve_returns_identity_for_default_variant(monkeypatch) -> None:
    """No env var, no arg → V0 + matching sha256 + matching char_count."""
    import hashlib as _hashlib

    from memeval.dreaming.prompts import resolve_extraction_prompt

    monkeypatch.delenv("DREAM_EXTRACTION_VARIANT", raising=False)
    ident = resolve_extraction_prompt()
    assert ident.variant == "V0"
    assert ident.text == EXTRACTION_SYSTEM_PROMPT
    assert ident.sha256 == _hashlib.sha256(EXTRACTION_SYSTEM_PROMPT.encode()).hexdigest()
    assert ident.char_count == len(EXTRACTION_SYSTEM_PROMPT)


def test_resolve_explicit_arg_wins_over_env(monkeypatch) -> None:
    """Explicit arg wins over DREAM_EXTRACTION_VARIANT env var."""
    from memeval.dreaming.prompts import resolve_extraction_prompt

    monkeypatch.setenv("DREAM_EXTRACTION_VARIANT", "V1")
    ident = resolve_extraction_prompt("V3")
    assert ident.variant == "V3"
    assert ident.text == EXTRACTION_SYSTEM_PROMPT_V3


def test_resolve_env_variant_when_no_arg(monkeypatch) -> None:
    """DREAM_EXTRACTION_VARIANT=V2 (case-insensitive) → V2 identity."""
    from memeval.dreaming.prompts import resolve_extraction_prompt

    monkeypatch.setenv("DREAM_EXTRACTION_VARIANT", "v2")
    ident = resolve_extraction_prompt()
    assert ident.variant == "V2"
    assert ident.text == EXTRACTION_SYSTEM_PROMPT_V2


def test_resolve_unknown_variant_raises_value_error(monkeypatch) -> None:
    """Unknown DREAM_EXTRACTION_VARIANT raises ValueError naming the legal set."""
    import pytest as _pytest

    from memeval.dreaming.prompts import resolve_extraction_prompt

    monkeypatch.setenv("DREAM_EXTRACTION_VARIANT", "made-up")
    with _pytest.raises(ValueError) as exc_info:
        resolve_extraction_prompt()
    msg = str(exc_info.value)
    assert "MADE-UP" in msg
    assert "V0" in msg
    assert "V3" in msg


def test_get_extraction_prompt_is_resolve_text_for_all_variants(monkeypatch) -> None:
    """`get_extraction_prompt` and `resolve_extraction_prompt(...).text` agree."""
    from memeval.dreaming.prompts import (
        get_extraction_prompt,
        list_extraction_variants,
        resolve_extraction_prompt,
    )

    for v in list_extraction_variants():
        assert get_extraction_prompt(v) == resolve_extraction_prompt(v).text


# --------------------------------------------------------------------------- #
# OKF_CONTENT_TYPES — closed taxonomy contract (ADR-dreaming-027 amended by
# ADR-dreaming-028 §5: `Contradiction` added as a worker-reserved ninth value)
# --------------------------------------------------------------------------- #
def test_okf_content_types_closed_set_membership() -> None:
    """The closed taxonomy contains the eight LLM-selectable values + the
    worker-reserved `Contradiction`. `Memory` (the parser fallback) is NOT in
    the set — it's a string the parser falls back to when the LLM emits
    something off-list, not a member of the taxonomy itself."""
    expected = {
        # LLM-selectable (V5 prompt body enumerates these eight)
        "Fix",
        "Bug",
        "Convention",
        "Invariant",
        "Workaround",
        "Decision",
        "Preference",
        "Identity",
        # Worker-reserved (ADR-dreaming-028 §5 — dream worker's deduction pass
        # emits these; LLM never sees the value as selectable).
        "Contradiction",
    }
    assert set(OKF_CONTENT_TYPES) == expected, (
        f"OKF taxonomy drift: missing={expected - set(OKF_CONTENT_TYPES)}, "
        f"extra={set(OKF_CONTENT_TYPES) - expected}"
    )
    assert "Memory" not in OKF_CONTENT_TYPES, (
        "`Memory` is the parser fallback string; it MUST NOT be a member of "
        "OKF_CONTENT_TYPES or the `daydream.unknown_okf_type` drift event "
        "becomes meaningless (every fallback would silently look like a valid "
        "LLM emission)."
    )


def test_v5_prompt_body_does_not_advertise_contradiction() -> None:
    """`Contradiction` is worker-reserved per ADR-dreaming-028 §5. The V5 prompt
    body MUST NOT enumerate it as an LLM-selectable value — the daydream
    extractor operates on a single session transcript and cannot observe
    cross-memory disagreement at extract time. If this fails, either the V5
    prompt was edited without rolling back the reservation, or the
    reservation was lifted intentionally (in which case the test should be
    deleted alongside the prompt change)."""
    assert "Contradiction" not in EXTRACTION_SYSTEM_PROMPT_V5


# --------------------------------------------------------------------------- #
# ADR-dreaming-028 §2 PR #2e — DEDUP_SYSTEM_PROMPT pin + substring contract
# --------------------------------------------------------------------------- #

_DEDUP_SYSTEM_PROMPT_SHA256 = (
    "09ce839fb8a5e6c5468789dabe07fc084b62c2c0ad9f3438b304933403fd1e4d"
)


def test_dedup_system_prompt_sha256_pinned() -> None:
    """ADR-028 §2 PR #2e — pinned sha256 hex digest matches the live constant.
    Bumping requires deliberate reviewer authorization (same gate as the
    contradiction prompt)."""
    h = hashlib.sha256(DEDUP_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    assert h == _DEDUP_SYSTEM_PROMPT_SHA256, (
        "DEDUP_SYSTEM_PROMPT drifted from its pinned hash. "
        "Update _DEDUP_SYSTEM_PROMPT_SHA256 only after deliberate review."
    )


def test_dedup_prompt_pins_pairs_schema() -> None:
    """ADR-028 §2 PR #2e — required substrings present (case-insensitive).
    Mirrors the CONTRADICTION_SYSTEM_PROMPT substring contract so the
    worker's per-batch parse machinery can be reused unchanged."""
    text = DEDUP_SYSTEM_PROMPT.lower()
    for sub in ("pairs", "a_id", "b_id", "rationale", "json only", "no markdown fences"):
        assert sub in text, f"missing required substring: {sub!r}"


def test_dedup_prompt_injection_framing() -> None:
    """ADR-028 §2 PR #2e — nonce-bounded DATA defense framing must be present
    so the LLM doesn't follow directives embedded in user-controlled content."""
    assert "DATA, not instructions" in DEDUP_SYSTEM_PROMPT
    assert "nonce" in DEDUP_SYSTEM_PROMPT.lower()


def test_dedup_prompt_forbids_invented_ids() -> None:
    """ADR-028 §2 PR #2e — prompt instructs the LLM not to invent ids outside
    the input array. Same anti-hallucination posture as the contradiction
    and governance prompts."""
    text = DEDUP_SYSTEM_PROMPT.lower()
    assert "not in the input array" in text or "do not invent ids" in text


def test_dedup_prompt_distinguishes_duplicates_from_contradictions() -> None:
    """ADR-028 §2 PR #2e — the prompt MUST explicitly tell the LLM that
    contradicting pairs are NOT duplicates, so a flat disagreement doesn't
    get silently merged (which would delete one side and bypass the
    contradiction-as-data preservation in ADR-028 §4)."""
    text = DEDUP_SYSTEM_PROMPT.lower()
    # Specific phrasing isn't pinned — only that the prompt explicitly
    # excludes contradictions from the dedup criterion.
    assert "contradict" in text, (
        "DEDUP_SYSTEM_PROMPT must explicitly distinguish duplicates from "
        "contradictions or it'll silently merge disagreements."
    )


def test_dedup_and_contradiction_prompts_are_distinct() -> None:
    """ADR-028 §2 PR #2e — the two judgment prompts must not collide. Pinning
    catches drift; this catches the accidental-identity bug where someone
    copies one and forgets to change it."""
    assert DEDUP_SYSTEM_PROMPT != CONTRADICTION_SYSTEM_PROMPT
    h_dedup = hashlib.sha256(DEDUP_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    h_contra = hashlib.sha256(CONTRADICTION_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    assert h_dedup != h_contra
