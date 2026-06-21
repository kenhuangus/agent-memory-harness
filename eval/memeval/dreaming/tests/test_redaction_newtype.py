"""``RedactedText`` NewType tests (rubric §N) — ADR-dreaming-010.

The subprocess-driven mypy negative/positive typecheck tests (criteria #74,
#75) require an additional fixture path under ``tests/typecheck/`` and are
intentionally deferred to a follow-up commit on this branch — they need
mypy installed AND a sane `MYPY_CACHE_DIR` strategy. The criterion's
non-vacuous shape is reachable by `make typecheck` per criterion #76.

This file covers the runtime-observable parts of ADR-010:
- NewType is defined and has the expected metadata.
- Public re-export works.
- ``redact()`` returns a value that IS a str at runtime (NewType is a
  type-checker fiction).
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "detect_secrets",
    reason="install with `pip install -e eval[daydream]` to run redaction tests",
)


def test_redactedtext_is_newtype_of_str():
    from memeval.dreaming.redaction import RedactedText

    assert RedactedText.__supertype__ is str
    assert RedactedText.__name__ == "RedactedText"


def test_redactedtext_public_import():
    """The conventional consumer import path resolves."""
    from memeval.dreaming.redaction import RedactedText  # noqa: F401


def test_redact_return_value_is_str_at_runtime():
    """NewType is type-checker only; at runtime it's a str."""
    from memeval.dreaming.redaction import redact

    out = redact("no secrets")
    assert isinstance(out, str)
    assert out == "no secrets"


def test_redactedtext_constructor_returns_str():
    """RedactedText('x') returns a plain str at runtime (NewType identity)."""
    from memeval.dreaming.redaction import RedactedText

    wrapped = RedactedText("hello")
    assert wrapped == "hello"
    assert isinstance(wrapped, str)
