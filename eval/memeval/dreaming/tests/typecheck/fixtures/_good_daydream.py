"""Fixture: should PASS mypy --strict.

Demonstrates that `redact()` → `_wrap_user_content_in_envelope` →
`LLMClient.complete(prompt=...)` is type-correct end-to-end — the
`RedactedText` NewType is preserved across the wrap so the LLM client
boundary (ADR-dreaming-010) accepts the envelope-wrapped payload.

Rubric §T criterion 160 (F1): mypy positive — wrapped envelope is
accepted at `client.complete(prompt=...)` without any cast.

Not test-collected by pytest (leading underscore, no test_ prefix).
"""

from __future__ import annotations

from memeval.dreaming._extract import _wrap_user_content_in_envelope, extract_memories
from memeval.dreaming.llm import LLMClient
from memeval.dreaming.redaction import RedactedText, redact
from memeval.schema import MemoryItem


def positive(client: LLMClient) -> None:
    """Exercises the RedactedText flow through the envelope into client.complete."""
    raw: str = "hello"
    cleaned: RedactedText = redact(raw)
    wrapped: RedactedText = _wrap_user_content_in_envelope(
        cleaned, session_id="s", now=0.0
    )
    # client.complete must accept the wrapped RedactedText without any cast.
    completion = client.complete(prompt=wrapped, system=None, max_tokens=100)
    _ = completion.text

    # extract_memories also accepts the wrapped RedactedText as its chunk arg.
    items: list[MemoryItem] | None = extract_memories(
        wrapped,
        client=client,
        session_id="s",
        now=0.0,
        id_gen=lambda: "mem_deadbeef",
        max_tokens=100,
    )
    _ = items
