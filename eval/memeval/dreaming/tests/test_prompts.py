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
    EXTRACTION_SYSTEM_PROMPT,
    _ENVELOPE_TEMPLATE,
)

# Computed at write time. Bumping requires deliberate reviewer authorization.
_CONTRADICTION_SYSTEM_PROMPT_SHA256 = (
    "25cd0ad0222a9b2c94b6399957fefe5b8a0dc7108f3012d2a183c77a31c7b4c6"
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
        "b928a726cc5509ee35d2c6774aa9ef0bae829ac0e2d9cca8b633add7da213e47"
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
