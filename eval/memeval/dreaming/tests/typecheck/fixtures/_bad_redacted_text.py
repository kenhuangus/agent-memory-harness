"""Fixture: should FAIL mypy --strict because a raw str is passed to a function
annotated as accepting only RedactedText. Not test-collected by pytest (leading
underscore, no test_ prefix).
"""

from memeval.dreaming.redaction import RedactedText


def consume(x: RedactedText) -> None:
    pass


consume("raw string is not RedactedText")  # MYPY ERROR: arg-type
