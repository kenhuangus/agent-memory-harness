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


def _have_mypy() -> bool:
    """Return True iff mypy is importable or on PATH."""
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
    """Invoke `mypy --strict` on `target` and capture stdout+stderr."""
    return subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(target)],
        capture_output=True,
        text=True,
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
