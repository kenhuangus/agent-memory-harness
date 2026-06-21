"""Rubric §E #29-31 — lazy-import discipline for detect_secrets.

Architecture §3 forbids third-party packages from loading at module top.
These tests subprocess Python with clean state so "fresh import" behavior
is actually fresh (no test pollution from other tests in the same process).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

# These tests don't need detect-secrets to be installed in the parent
# process — they subprocess out. They DO need memeval to be installed
# (editable) so the child can import it.


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
    )


def test_module_imports_without_detect_secrets() -> None:
    """§29: importing the redaction package succeeds even when
    detect_secrets is not importable.

    Simulates "not installed" by stubbing sys.modules with None BEFORE the
    import — any subsequent `import detect_secrets` raises ImportError.
    """
    code = """
        import sys
        # Make detect_secrets unimportable.
        sys.modules['detect_secrets'] = None
        # The redaction package itself must NOT need detect_secrets at
        # import time. Heavy deps load inside redact() only.
        import memeval.dreaming.redaction  # noqa: F401
        print("OK")
        """
    result = _run(code)
    assert result.returncode == 0, (
        f"redaction package failed to import without detect_secrets; "
        f"the lazy-import rule is broken.\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "OK" in result.stdout


def test_redaction_import_does_not_load_detect_secrets() -> None:
    """§30: after `import memeval.dreaming.redaction`, detect_secrets is NOT
    in sys.modules. Architecture §3: no eager third-party load.
    """
    code = """
        import sys
        import memeval.dreaming.redaction  # noqa: F401
        assert 'detect_secrets' not in sys.modules, (
            "detect_secrets was loaded eagerly by the redaction package import"
        )
        print("OK")
        """
    result = _run(code)
    assert result.returncode == 0, (
        f"detect_secrets was loaded eagerly.\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "OK" in result.stdout


def test_redact_call_raises_clear_importerror_when_detect_secrets_missing() -> None:
    """§37: if detect_secrets is uninstallable at call time, redact() raises a
    clear ImportError naming the `daydream` extra — not a silent no-op."""
    code = """
        import sys
        # Make detect_secrets unimportable.
        sys.modules['detect_secrets'] = None
        # Also pre-poison the submodules so the inner `from detect_secrets.plugins.x
        # import Y` calls fail immediately rather than walking the import system.
        for name in list(sys.modules):
            if name.startswith('detect_secrets'):
                sys.modules[name] = None

        from memeval.dreaming.redaction import redact
        try:
            redact("any text")
        except ImportError as e:
            msg = str(e)
            if 'daydream' not in msg:
                print(f"FAIL: ImportError did not name 'daydream' extra: {msg!r}")
                sys.exit(2)
            print("OK")
            sys.exit(0)
        except BaseException as e:
            print(f"FAIL: redact raised non-ImportError {type(e).__name__}: {e}")
            sys.exit(3)
        else:
            print("FAIL: redact did not raise; silent no-op is forbidden")
            sys.exit(1)
        """
    result = _run(code)
    assert result.returncode == 0, (
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "OK" in result.stdout


def test_redact_call_triggers_detect_secrets_import() -> None:
    """§31: the first redact() call DOES load detect_secrets (the lazy
    import fires). Confirms §30 doesn't silently mean we never load it.
    """
    code = """
        import sys
        from memeval.dreaming.redaction import redact
        assert 'detect_secrets' not in sys.modules, (
            "detect_secrets was loaded by the symbol import; lazy rule broken"
        )
        redact("x")
        assert 'detect_secrets' in sys.modules, (
            "detect_secrets did NOT load after redact(); lazy import never fires"
        )
        print("OK")
        """
    result = _run(code)
    # If detect_secrets isn't installed in this venv, the test is vacuous.
    if result.returncode != 0 and "ImportError" in (result.stderr or "") + (
        result.stdout or ""
    ):
        pytest.skip("detect_secrets not installed in this venv; cannot verify lazy fire")
    assert result.returncode == 0, (
        f"lazy-import test failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "OK" in result.stdout
