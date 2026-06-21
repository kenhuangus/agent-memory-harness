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
from memeval.dreaming.prompts import EXTRACTION_SYSTEM_PROMPT, _ENVELOPE_TEMPLATE
from memeval.dreaming.redaction import RedactedText
from memeval.schema import MemoryItem

_logger = logging.getLogger(__name__)

__all__ = [
    "extract_memories",
    "_ParseError",
]

#: Memory `content` strings longer than this are rejected at parse time per
#: plan §3 "non-empty string ≤ 200 chars". Cap matches the implicit ceiling
#: in PR4 plan's `_build_memory_item` spec.
_MAX_CONTENT_LEN = 200

#: Maximum number of tags retained on a single MemoryItem (plan §3 "0-5").
_MAX_TAGS = 5


class _ParseError(Exception):
    """Per-item parse failure inside `_build_memory_item`. Engine drops the row."""


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
    # REASON: developer-authored constant, no user content.
    system = RedactedText(EXTRACTION_SYSTEM_PROMPT)

    completion: Completion = client.complete(
        wrapped, system=system, max_tokens=max_tokens
    )

    if not completion.text:
        emit("chunk_skipped_unavailable_llm", session_id=session_id)
        return None

    try:
        data = json.loads(completion.text)
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

    if n_dropped:
        emit("chunk_partial_parse", n_kept=len(items), n_dropped=n_dropped)

    emit(
        "daydream.chunk_extracted",
        n_items=len(items),
        tokens_in=completion.tokens_in,
        tokens_out=completion.tokens_out,
        cost_usd=cost_of(client.model, completion.tokens_in, completion.tokens_out),
        model=client.model,
    )
    return items


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
    or wrong shape) and `relevancy` (float in [0, 1]; values outside
    are clamped; non-numeric falls back to the schema default of 1.0).
    Raises `_ParseError` on any required-field failure; caller drops the row.
    """
    if not isinstance(raw, dict):
        raise _ParseError(f"row is not a dict: {type(raw).__name__}")

    content = raw.get("content")
    if not isinstance(content, str) or not content or len(content) > _MAX_CONTENT_LEN:
        raise _ParseError("invalid or missing 'content'")

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
        metadata={"extracted_from": session_id},
    )
