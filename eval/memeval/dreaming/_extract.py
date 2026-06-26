"""Daydream LLM-extraction pipeline — envelope wrap + JSON parse → MemoryItem.

Wraps a `RedactedText` chunk in a nonce-tagged transcript envelope
(prompt-injection defense per ADR-010 + plan-v2 §5(k)), calls the swappable
`LLMClient.complete()` seam, parses the JSON response, and constructs the
list of `MemoryItem`s the engine persists. Returns `None` for any failure
that the engine must treat as a "do-not-advance-cursor" abort
(ADR-013): empty completion (ADR-012), JSON parse error, malformed
top-level shape. Returns `[]` for the real-empty case where the model
successfully decided nothing was extractable from this chunk.

Stdlib + dreaming-internal imports only at module top per
``architecture.md`` §3. Per-item parse failures are isolated by
`_ParseError` and counted into a `chunk_partial_parse` event so a single
bad row in the JSON does not silently drop the whole chunk.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any, Callable

from memeval.cost import cost_of
from memeval.dreaming.events import emit
from memeval.dreaming.llm import Completion, LLMClient
from memeval.dreaming.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    OKF_CONTENT_TYPES,
    _ENVELOPE_TEMPLATE,
    get_extraction_prompt,
    resolve_extraction_prompt,
)
from memeval.dreaming.redaction import RedactedText, redact
from memeval.schema import MemoryItem

_logger = logging.getLogger(__name__)

__all__ = [
    "extract_memories",
    "_ParseError",
    "_loads_lenient",
]

#: Memory `content` strings longer than this are rejected at parse time per
#: plan §3 "non-empty string ≤ 200 chars". Cap matches the implicit ceiling
#: in PR4 plan's `_build_memory_item` spec.
_MAX_CONTENT_LEN = 200

#: Maximum number of tags retained on a single MemoryItem (plan §3 "0-5").
_MAX_TAGS = 5

#: Rejection-event field caps (halliday-amended plan §3.2). Snippet is
#: deliberately tighter than `_MAX_CONTENT_LEN`: it's a forensic tracer, not
#: a stored memory, and the smaller cap bounds the residual-leak surface
#: (halliday B1 — even after the second `redact()` pass).
_REJECTION_SNIPPET_MAX_LEN: int = 100
_REJECTION_RATIONALE_MAX_LEN: int = 200

#: Per-chunk cap on emitted ``daydream.candidate_rejected`` events
#: (halliday B2). Overflow rows fold into ``rejected_n_dropped`` in the
#: extended ``chunk_partial_parse`` event so operators can detect
#: cap-hit. The LLM is told about this cap in `EXTRACTION_SYSTEM_PROMPT`
#: so it self-selects the most informative 50 instead of getting silently
#: truncated.
_REJECTION_MAX_PER_CHUNK: int = 50

#: One-shot guard for ``daydream.rejected_field_missing`` (halliday B3).
#: When an LLM completion omits the ``rejected`` key entirely (vs emitting
#: ``rejected: []``), the extractor fires this event ONCE per session_id
#: so a silent model regression cannot mask itself. Keyed on session_id;
#: cleared by process restart (no persistence — that's an upstream
#: concern). Module-level so the same set persists across calls within
#: a single Daydream pass.
_rejected_missing_seen: set[str] = set()


class _RejectedMissing:
    """Sentinel that distinguishes ``rejected`` key absent from
    ``rejected: []`` / ``rejected: <wrong-type>``. Halliday B3 needs the
    absent-key case to fire its own event exactly once per session."""

    __slots__ = ()


_REJECTED_MISSING = _RejectedMissing()


class _ParseError(Exception):
    """Per-item parse failure inside `_build_memory_item`. Engine drops the row."""


def _loads_lenient(text: str) -> Any:
    """``json.loads`` tolerant of code-fenced / prose-wrapped completions.

    Many models (e.g. ``deepseek/deepseek-chat``) ignore the "JSON only,
    no markdown fences" instruction and wrap their object in a ```json …```
    fence or precede it with prose. A bare ``json.loads`` on that throws and
    the whole chunk is silently dropped as ``chunk_skipped_parse_failed``
    despite a paid, successful LLM call.

    Recovery, in order: (1) ``json.loads`` the raw text; (2) strip a leading
    ```` ``` ```` / ```` ```json ```` fence and trailing ```` ``` ````, then
    parse; (3) fall back to the substring from the first ``{`` to the last
    ``}``. On genuinely non-JSON input every attempt fails and the final
    ``json.JSONDecodeError`` propagates, so the caller preserves its EXISTING
    ``chunk_skipped_parse_failed`` behavior exactly. Pure stdlib.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop the opening fence line (``` or ```json/```JSON) and the
        # closing ``` fence, then parse what's between.
        inner = stripped[3:]
        newline = inner.find("\n")
        if newline != -1:
            # Discard an optional language tag on the opening fence line.
            inner = inner[newline + 1 :]
        if inner.rstrip().endswith("```"):
            inner = inner.rstrip()[:-3]
        try:
            return json.loads(inner.strip())
        except json.JSONDecodeError:
            pass

    # Fallback: extract the outermost {...} span from surrounding prose.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        # Re-raises json.JSONDecodeError on failure → caller emits
        # chunk_skipped_parse_failed, exactly as before.
        return json.loads(text[start : end + 1])

    # Nothing recoverable — re-raise the original-style error for the caller.
    return json.loads(text)


def _default_id_gen() -> str:
    """Return a fresh `mem_` + 8 hex-char identifier derived from uuid4."""
    return "mem_" + uuid.uuid4().hex[:8]


def _wrap_user_content_in_envelope(
    redacted: RedactedText,
    *,
    session_id: str,
    now: float,
) -> RedactedText:
    """Wrap `redacted` in a session-unique nonce-tagged transcript envelope.

    The envelope template is developer-authored and the nonce is engine
    -derived from `sha256(session_id + str(now))[:8]` — only the inner
    content originated from the user (and is already `RedactedText`). The
    explicit `RedactedText(...)` cast is the documented ADR-010 §Open-items
    deliberate-bypass: wrapping a `RedactedText` in a frame stays
    `RedactedText`.
    """
    nonce = hashlib.sha256(f"{session_id}{now}".encode("utf-8")).hexdigest()[:8]
    wrapped = _ENVELOPE_TEMPLATE.format(nonce=nonce, redacted=str(redacted))
    # REASON: envelope is developer-authored + nonce is engine-generated;
    # only the inner content is user-derived and already RedactedText.
    return RedactedText(wrapped)


def extract_memories(
    redacted_chunk: RedactedText,
    *,
    client: LLMClient,
    session_id: str,
    now: float,
    id_gen: Callable[[], str],
    max_tokens: int = 2048,
) -> list[MemoryItem] | None:
    """Call the LLM on `redacted_chunk` and return parsed `MemoryItem`s.

    Returns `None` on any abort-without-cursor-advance path: empty
    completion text (ADR-012 unavailable provider), malformed JSON,
    non-dict top-level, missing `memories` key, or non-list `memories`
    value. Returns `[]` when the model successfully returned
    `{"memories": []}` — a real "nothing to extract" result that lets the
    engine advance the cursor past this chunk. Individual rows that fail
    `_build_memory_item` validation are dropped and counted into a
    `chunk_partial_parse` event; the surviving rows are still returned.
    """
    wrapped = _wrap_user_content_in_envelope(
        redacted_chunk, session_id=session_id, now=now
    )
    # Resolve the active extraction prompt variant per call (ADR-dreaming-023):
    # default V0 = EXTRACTION_SYSTEM_PROMPT, opt-in V1/V2/V3 via
    # DREAM_EXTRACTION_VARIANT env var. Reading per call (vs at import time)
    # lets operators flip variants without a process restart.
    # REASON: developer-authored constants, no user content.
    identity = resolve_extraction_prompt()
    system = RedactedText(identity.text)
    # Per-chunk forensic anchor: one event carries (variant, sha256, char_count,
    # model). Per-memory events stay lean; correlate via session_id + ts.
    emit(
        "daydream.prompt_resolved",
        session_id=session_id,
        variant=identity.variant,
        prompt_sha256=identity.sha256,
        prompt_chars=identity.char_count,
        model=client.model,
    )

    completion: Completion = client.complete(
        wrapped, system=system, max_tokens=max_tokens
    )
    # Per-call full-fidelity logging: full system prompt + full redacted user
    # content + full raw response. Fires UNCONDITIONALLY after the call returns
    # — so the empty-completion (#133-shape) case is also captured. Sized to
    # be the diagnostic surface developers actually need; intentionally NOT
    # redacted further at the emit seam beyond ADR-005's input redaction (the
    # diary is local-only per ADR-011 §Policy + dev-only debug per
    # ADR-dreaming-025). Identity (variant + sha256) on the record dedups via
    # the prompt_resolved breadcrumb above.
    emit(
        "daydream.llm_call",
        session_id=session_id,
        variant=identity.variant,
        prompt_sha256=identity.sha256,
        system_prompt=str(system),
        user_content=str(wrapped),
        response_text=completion.text,
        tokens_in=completion.tokens_in,
        tokens_out=completion.tokens_out,
        cost_usd=cost_of(client.model, completion.tokens_in, completion.tokens_out),
        model=client.model,
    )

    if not completion.text:
        emit("chunk_skipped_unavailable_llm", session_id=session_id)
        return None

    try:
        data = _loads_lenient(completion.text)
    except json.JSONDecodeError as exc:
        emit("chunk_skipped_parse_failed", reason=str(exc))
        return None

    if not isinstance(data, dict):
        emit(
            "chunk_skipped_parse_failed",
            reason=f"top-level not dict: {type(data).__name__}",
        )
        return None
    if "memories" not in data:
        emit("chunk_skipped_parse_failed", reason="missing 'memories' key")
        return None
    raw_memories = data["memories"]
    if not isinstance(raw_memories, list):
        emit(
            "chunk_skipped_parse_failed",
            reason=f"'memories' not list: {type(raw_memories).__name__}",
        )
        return None

    items: list[MemoryItem] = []
    n_dropped = 0
    for raw in raw_memories:
        try:
            items.append(
                _build_memory_item(
                    raw, session_id=session_id, now=now, id_gen=id_gen
                )
            )
        except _ParseError:
            n_dropped += 1

    # ── Rejection-candidate parse pass (halliday-amended plan §§B1/B2/B3 + A1/A2) ─
    # The LLM is asked to emit a parallel `rejected` array carrying the
    # candidates it considered-but-dropped, with a snippet + rationale per
    # row. Each surviving row produces one ``daydream.candidate_rejected``
    # event. The block accounts for: missing-key (B3), cap-overflow (B2),
    # per-row malformed (A1 family), normalized overlap with a kept memory
    # (A1), second-pass redaction of `content_snippet` (B1), and explicit
    # truncation flags on the event payload (A2).
    raw_rejected: Any = data.get("rejected", _REJECTED_MISSING)
    rejected_n_dropped = 0
    if isinstance(raw_rejected, _RejectedMissing):
        # B3: silent regression masking is the failure mode this guards.
        # Fire the warning ONCE per session_id (set is module-level).
        if session_id not in _rejected_missing_seen:
            emit("daydream.rejected_field_missing", session_id=session_id)
            _rejected_missing_seen.add(session_id)
        raw_rejected = []
    elif not isinstance(raw_rejected, list):
        # Coerce wrong-type to empty, matching the leniency we already grant
        # to malformed `tags` per ``_build_memory_item``. Not a parse failure.
        raw_rejected = []

    # A1: precompute the normalized kept-memory content set so a rejection
    # event that overlaps a kept memory is suppressed (operator forensic
    # value collapses if the same content appears in both surfaces).
    kept_content_norms: set[str] = {
        item.content.strip().lower()[:_REJECTION_SNIPPET_MAX_LEN]
        for item in items
        if item.content
    }
    rejected_n_kept = 0
    for batch_index, raw_rej in enumerate(raw_rejected):
        if batch_index >= _REJECTION_MAX_PER_CHUNK:
            # B2: prompt advertises the 50-cap; overflow counts but doesn't emit.
            rejected_n_dropped += 1
            continue
        if not isinstance(raw_rej, dict):
            rejected_n_dropped += 1
            continue
        snippet_raw = raw_rej.get("content_snippet")
        rationale_raw = raw_rej.get("rationale")
        if not isinstance(snippet_raw, str) or not isinstance(rationale_raw, str):
            rejected_n_dropped += 1
            continue
        # B1: SECOND-PASS redact() on snippet BEFORE truncation+emit. ADR-005
        # guarantees redaction on the INPUT side; the LLM may quote unredacted
        # input back in `content_snippet`. The local-regex second pass closes
        # the residual-leak surface at minimal cost (no LLM call).
        snippet_redacted = str(redact(snippet_raw))
        # A1: suppress events whose normalized snippet overlaps a kept memory.
        if snippet_redacted.strip().lower()[:_REJECTION_SNIPPET_MAX_LEN] in kept_content_norms:
            rejected_n_dropped += 1
            continue
        snippet_truncated = len(snippet_redacted) > _REJECTION_SNIPPET_MAX_LEN
        rationale_truncated = len(rationale_raw) > _REJECTION_RATIONALE_MAX_LEN
        emit(
            "daydream.candidate_rejected",
            content_snippet=snippet_redacted[:_REJECTION_SNIPPET_MAX_LEN],
            rationale=rationale_raw[:_REJECTION_RATIONALE_MAX_LEN],
            session_id=session_id,
            batch_index=batch_index,
            snippet_truncated=snippet_truncated,
            rationale_truncated=rationale_truncated,
        )
        rejected_n_kept += 1

    if n_dropped or rejected_n_dropped:
        # `chunk_partial_parse` extended with parallel rejected-side counters
        # (halliday-amended plan §3.3). The existing `n_kept`/`n_dropped`
        # remain memories-only; the new kwargs partition the rejection drops
        # so an operator can distinguish memory-row malformations from
        # rejection-row malformations / cap-overflow / overlap suppressions.
        emit(
            "chunk_partial_parse",
            n_kept=len(items),
            n_dropped=n_dropped,
            rejected_n_kept=rejected_n_kept,
            rejected_n_dropped=rejected_n_dropped,
        )

    emit(
        "daydream.chunk_extracted",
        n_items=len(items),
        tokens_in=completion.tokens_in,
        tokens_out=completion.tokens_out,
        cost_usd=cost_of(client.model, completion.tokens_in, completion.tokens_out),
        model=client.model,
    )
    return items


#: ``okf_type`` value used when the LLM did not supply one (pre-V5 variants)
#: OR when the LLM supplied a value outside ``OKF_CONTENT_TYPES`` (off-list
#: drift). The OKF serializer (``eval/memeval/okf.py``) treats ``Memory``
#: as its generic fallback per ADR-dreaming-027.
_OKF_TYPE_FALLBACK: str = "Memory"


def _build_memory_item(
    raw: dict[str, Any],
    *,
    session_id: str,
    now: float,
    id_gen: Callable[[], str],
) -> MemoryItem:
    """Validate `raw` and construct one `MemoryItem` with engine-supplied defaults.

    Required: `raw` must be a dict with key `content` whose value is a
    non-empty string of length ≤ `_MAX_CONTENT_LEN`. Optional: `tags`
    (list of strings, kept up to `_MAX_TAGS`; defaults to `[]` if absent
    or wrong shape), `relevancy` (float in [0, 1]; values outside are
    clamped; non-numeric falls back to the schema default of 1.0), and
    `type` (one of `OKF_CONTENT_TYPES` per ADR-dreaming-027; off-list
    or missing → `_OKF_TYPE_FALLBACK`; an off-list value additionally
    emits ``daydream.unknown_okf_type`` for observability — a missing
    value does NOT emit, since pre-V5 prompts legitimately omit the
    field). Raises `_ParseError` on any required-field failure; caller
    drops the row.
    """
    if not isinstance(raw, dict):
        raise _ParseError(f"row is not a dict: {type(raw).__name__}")

    content = raw.get("content")
    if not isinstance(content, str) or not content or len(content) > _MAX_CONTENT_LEN:
        raise _ParseError("invalid or missing 'content'")
    # Second-pass redact() on kept-memory content. ADR-005 only guarantees
    # redaction on the INPUT side; the LLM may echo unredacted user text back
    # in `content`. The rejection path already does this for `content_snippet`
    # (halliday B1) — kept content is even higher-stakes because it gets
    # persisted to the store AND recalled into future LLM contexts. The cap
    # check above applies to the LLM-emitted length; redaction tokens may
    # grow or shrink it. Surfaced by CodeRabbit on PR #137.
    content = str(redact(content))

    raw_tags = raw.get("tags", [])
    if isinstance(raw_tags, list):
        tags = [t for t in raw_tags if isinstance(t, str)][:_MAX_TAGS]
    else:
        tags = []

    raw_relevancy = raw.get("relevancy", 1.0)
    if isinstance(raw_relevancy, (int, float)) and not isinstance(raw_relevancy, bool):
        relevancy = max(0.0, min(1.0, float(raw_relevancy)))
    else:
        relevancy = 1.0

    # OKF content type per ADR-dreaming-027. V5+ prompts emit `type` from
    # the closed `OKF_CONTENT_TYPES` set; older variants omit the field. We
    # distinguish three cases to keep the observability surface honest:
    #   - in-set string  → use as-is.
    #   - off-list value → fall back + emit `daydream.unknown_okf_type` so
    #                      operators can measure how often the LLM drifts.
    #   - missing/empty  → fall back silently (pre-V5 variants legitimately
    #                      do not emit a `type`; counting them as drift
    #                      would flood the event surface during V0-V4 runs).
    raw_type = raw.get("type")
    if isinstance(raw_type, str) and raw_type in OKF_CONTENT_TYPES:
        okf_type = raw_type
    elif isinstance(raw_type, str) and raw_type:
        emit(
            "daydream.unknown_okf_type",
            session_id=session_id,
            offending_value=raw_type[:_REJECTION_SNIPPET_MAX_LEN],
        )
        okf_type = _OKF_TYPE_FALLBACK
    else:
        okf_type = _OKF_TYPE_FALLBACK

    return MemoryItem(
        item_id=id_gen(),
        content=content,
        timestamp=now,
        relevancy=relevancy,
        session_id=session_id,
        source="daydream",
        tags=tags,
        embedding=None,
        tokens=0,
        version=1,
        metadata={"extracted_from": session_id, "okf_type": okf_type},
    )
