"""Rubric §N #74 + #75 — RedactedText NewType is enforced by mypy --strict.

These tests subprocess-out to mypy so the actual type checker runs against
fixture files. Without these, ADR-dreaming-010's structural-enforcement
promise is only a hope.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip(
    "detect_secrets",
    reason="install with `pip install -e eval[daydream]` to run redaction tests",
)

FIXTURES = Path(__file__).parent / "fixtures"
_BAD = FIXTURES / "_bad_redacted_text.py"
_GOOD = FIXTURES / "_good_redacted_text.py"


def _have_mypy() -> bool:
    if shutil.which("mypy"):
        return True
    try:
        import mypy  # noqa: F401
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(
    not _have_mypy(),
    reason="mypy not installed — install with `pip install -e eval[dev]`",
)


def _run_mypy(target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(target)],
        capture_output=True,
        text=True,
    )


def test_redactedtext_negative_typecheck() -> None:
    """§74: a raw str passed to a RedactedText-annotated function fails mypy.

    If this passes (mypy returns 0), the structural enforcement is broken —
    either RedactedText is misdefined or mypy isn't actually running strict.
    """
    result = _run_mypy(_BAD)
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode != 0, (
        f"mypy unexpectedly passed on a bad fixture; the structural "
        f"enforcement is broken. Output:\n{output}"
    )
    assert "RedactedText" in output, (
        f"mypy failed but the error didn't mention RedactedText; the "
        f"failure is unrelated to the type boundary. Output:\n{output}"
    )


def test_redactedtext_positive_typecheck() -> None:
    """§75: redact()'s return value satisfies a RedactedText parameter.

    Guards against §74 passing vacuously because mypy is misconfigured /
    rejects everything.
    """
    result = _run_mypy(_GOOD)
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, (
        f"mypy rejected the positive fixture — the RedactedText contract "
        f"is too tight (redact() return type isn't RedactedText), or mypy "
        f"can't resolve the memeval package. Output:\n{output}"
    )
