"""Rubric §P #137 + §T #160 — mypy --strict ACCEPTS the daydream positive fixture.

Subprocesses out to `mypy --strict` so the actual type checker runs against
`fixtures/_good_daydream.py`. Mirror of PR1's `test_redacted_text_typecheck.py`
positive driver. Without this, criterion 136's negative test could pass
vacuously (mypy misconfigured / rejects everything).

Plan-v2 §3 + §6.L: the RedactedText NewType must flow un-laundered from
`redact()` through `_wrap_user_content_in_envelope` to `client.complete()` —
this is the F1 remediation. If the wrapping function ever returns `str`
instead of `RedactedText`, this fixture stops type-checking.
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
_GOOD = FIXTURES / "_good_daydream.py"


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


def test_daydream_positive_typechecks() -> None:
    """§137 + §160: redact() → _wrap_user_content_in_envelope → client.complete
    type-checks under mypy --strict without any cast.

    Failure means either `_wrap_user_content_in_envelope` no longer returns
    `RedactedText`, or `LLMClient.complete`'s `prompt` parameter has lost
    its `RedactedText` annotation, or the memeval package isn't on the
    PYTHONPATH for mypy to resolve.
    """
    result = _run_mypy(_GOOD)
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, (
        f"mypy rejected the positive daydream fixture — the RedactedText "
        f"contract is broken somewhere along redact() → "
        f"_wrap_user_content_in_envelope → client.complete(prompt=...). "
        f"Output:\n{output}"
    )
