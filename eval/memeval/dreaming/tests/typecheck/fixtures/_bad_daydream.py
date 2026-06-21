"""Fixture: should FAIL mypy --strict with an arg-type error naming RedactedText.

A raw `str` passed to `LLMClient.complete(prompt=...)` violates the
ADR-dreaming-010 NewType boundary; mypy --strict must reject it.

Rubric §P criterion 136 (also §T criterion 160 negative half): mypy
negative — raw str to client.complete is rejected.

Not test-collected by pytest (leading underscore, no test_ prefix).
"""

from __future__ import annotations

from memeval.dreaming.llm import LLMClient


def negative(client: LLMClient) -> None:
    """Passes a raw `str` to `client.complete` — mypy MUST reject this."""
    raw_str: str = "this is just a str, not RedactedText"
    # MYPY ERROR: arg-type — incompatible type "str"; expected "RedactedText".
    completion = client.complete(prompt=raw_str, system=None, max_tokens=100)
    _ = completion.text
