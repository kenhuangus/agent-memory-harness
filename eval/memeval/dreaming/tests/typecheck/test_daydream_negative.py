"""Rubric §P #136 — mypy --strict REJECTS a raw str passed to client.complete.

Subprocesses out to `mypy --strict` so the actual type checker runs against
`fixtures/_bad_daydream.py`. Mirror of PR1's `test_redacted_text_typecheck.py`
negative driver. If this passes (mypy exits 0), the structural enforcement
of ADR-dreaming-010 is broken — either `RedactedText` was widened back to
`str` or `LLMClient.complete`'s `prompt` parameter lost its NewType
annotation.

The error message must mention `RedactedText` so the failure is unambiguously
the NewType boundary and not some unrelated mypy error swallowing the test.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip(
    "detect_secrets",
    reason="install with `pip install -e eval[daydream]` to run daydream typecheck tests",
)

FIXTURES = Path(__file__).parent / "fixtures"
_BAD = FIXTURES / "_bad_daydream.py"


def _mypy_cmd() -> list[str] | None:
    """Return the command prefix for mypy, or None when unavailable.

    CodeRabbit PR #42 finding: the prior ``_have_mypy()`` returned True if
    mypy was importable OR on PATH, but ``_run_mypy`` always invoked
    ``python -m mypy`` (importable-only). In an env where mypy is on PATH
    but not importable, tests would fail instead of skip. Returning the
    actual command lets the test use whichever invocation is available.
    """
    path_cmd = shutil.which("mypy")
    if path_cmd:
        return [path_cmd]
    try:
        import mypy  # noqa: F401
    except ImportError:
        return None
    return [sys.executable, "-m", "mypy"]


_MYPY_CMD = _mypy_cmd()
pytestmark = pytest.mark.skipif(
    _MYPY_CMD is None,
    reason="mypy not installed — install with `pip install -e eval[dev]`",
)


def _run_mypy(target: Path) -> subprocess.CompletedProcess[str]:
    """Invoke `mypy --strict` on `target` and capture stdout+stderr."""
    assert _MYPY_CMD is not None  # skipif guarantees this
    return subprocess.run(
        [*_MYPY_CMD, "--strict", str(target)],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_daydream_negative_fails_typecheck() -> None:
    """§136: a raw str passed to `client.complete(prompt=...)` fails mypy.

    If this passes (mypy returns 0), the structural enforcement of the
    RedactedText NewType is broken — the LLM-client trust boundary
    (ADR-dreaming-010) is no longer policed at type-check time.
    """
    result = _run_mypy(_BAD)
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode != 0, (
        f"mypy unexpectedly accepted a raw str at client.complete(prompt=...);"
        f" the RedactedText NewType boundary is broken. Output:\n{output}"
    )
    assert "RedactedText" in output, (
        f"mypy failed but the error didn't mention RedactedText; the "
        f"failure is unrelated to the NewType boundary. Output:\n{output}"
    )
