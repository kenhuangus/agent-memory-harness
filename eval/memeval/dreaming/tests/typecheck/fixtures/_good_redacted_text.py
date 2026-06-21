"""Fixture: should PASS mypy --strict — redact()'s return is RedactedText, which
satisfies the consume() parameter. Not test-collected by pytest.
"""

from memeval.dreaming.redaction import RedactedText, redact


def consume(x: RedactedText) -> None:
    pass


consume(redact("hello world"))
