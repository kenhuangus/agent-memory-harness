"""Behavioral tests for ``memeval.dreaming.redaction.redact``.

Covers rubric sections A (module shape), B (structured plugin coverage,
spot-check), D (entropy exclusion), F (fail-open), G (network isolation),
H (replacement contract), I (driving-mechanism constraints).

The exhaustive 11-of-11 structured plugin tests and per-custom-plugin
behavior live in ``test_redaction_plugins.py``; the audit-writer tests in
``test_redaction_audit.py``; the ``RedactedText`` NewType tests in
``test_redaction_newtype.py``.
"""

from __future__ import annotations

import inspect
import socket
import typing

import pytest

pytest.importorskip(
    "detect_secrets",
    reason="install with `pip install -e eval[daydream]` to run redaction tests",
)

from memeval.dreaming.redaction import RedactedText, redact


# --- A. module shape & public surface ------------------------------------- #
def test_public_surface_import():
    from memeval.dreaming.redaction import RedactedText as RT
    from memeval.dreaming.redaction import redact as r
    assert callable(r)
    assert RT is RedactedText


def test_redact_signature_is_frozen():
    # Resolve string annotations from `from __future__ import annotations`.
    hints = typing.get_type_hints(redact)
    sig = inspect.signature(redact)
    params = list(sig.parameters.values())
    assert len(params) == 1
    assert params[0].name == "text"
    assert hints["text"] is str
    # Return annotation must be RedactedText (NewType), not bare str.
    assert hints["return"] is RedactedText


def test_redact_empty_string_returns_empty():
    out = redact("")
    assert out == ""
    assert isinstance(out, str)


def test_redact_return_type_is_str():
    # NewType is a str at runtime.
    out = redact("nothing to redact")
    assert isinstance(out, str)


# --- B. structured plugin coverage (spot-check; full coverage in plugins file) #
def test_redact_replaces_aws_key():
    line = "User pasted AKIAIOSFODNN7EXAMPLE."
    out = redact(line)
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "[REDACTED:" in out


def test_redact_replaces_jwt():
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    out = redact(f"token: {jwt}")
    assert jwt not in out
    assert "[REDACTED:" in out


def test_redact_replaces_private_key():
    line = "-----BEGIN RSA PRIVATE KEY-----"
    out = redact(line)
    assert "[REDACTED:" in out


# --- D. entropy-detector exclusion --------------------------------------- #
def test_redact_does_not_false_positive_on_prose_example():
    """The spike's documented failure case must not be triggered.

    Default ``Base64HighEntropyString`` settings flagged every English word
    >=4 chars when driven via ``scan_line`` + ``default_settings``. Our
    curated list excludes the entropy detectors entirely (ADR-005 §2), so
    prose must pass through unchanged.
    """
    prose = "User pasted their AWS access key in chat."
    assert redact(prose) == prose


def test_active_plugins_exclude_entropy_detectors():
    """Introspect the cached plugin list to confirm no entropy detectors."""
    from memeval.dreaming import redaction
    # Force lazy build.
    redact("trigger")
    plugin_class_names = {type(p).__name__ for p in redaction._get_plugins()}
    assert "Base64HighEntropyString" not in plugin_class_names
    assert "HexHighEntropyString" not in plugin_class_names


# --- F. fail-open behavior ----------------------------------------------- #
@pytest.mark.parametrize(
    "text",
    [
        "",
        "x",
        "a" * 100_000,
        "line1\r\nline2",
        "line1\nline2\nline3",
        "unicode: café ☕ 日本語",
        "with NUL\x00inside",
    ],
)
def test_redact_never_raises(text):
    out = redact(text)
    assert isinstance(out, str)


def test_analyze_line_exception_is_logged_and_skipped(caplog, monkeypatch):
    """If one plugin's analyze_line() raises, other plugins still run."""
    import logging

    from memeval.dreaming import redaction

    # Force plugin list to build.
    redact("warmup")
    plugins = redaction._get_plugins()
    assert len(plugins) >= 2, "need at least two plugins to test isolation"

    # Pick a non-AWS plugin to break so AWSKeyDetector still catches the
    # secret in the test text. Plugins[-1] is the last custom plugin.
    bad = next(p for p in plugins if type(p).__name__ != "AWSKeyDetector")
    original = bad.analyze_line

    def boom(*args, **kwargs):
        raise RuntimeError("simulated plugin failure")

    monkeypatch.setattr(bad, "analyze_line", boom)
    caplog.set_level(logging.WARNING, logger="memeval.dreaming.redaction")

    # AWS key — caught by AWSKeyDetector, which is NOT the broken one.
    out = redact("here's an AWS key AKIAIOSFODNN7EXAMPLE in chat")

    # The healthy AWSKeyDetector still redacted.
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    # The broken plugin's failure was logged.
    assert any(
        type(bad).__name__ in rec.getMessage() and "RuntimeError" in rec.getMessage()
        for rec in caplog.records
    )

    # Restore so other tests aren't affected.
    monkeypatch.setattr(bad, "analyze_line", original)


def test_redact_does_not_swallow_keyboardinterrupt(monkeypatch):
    """KeyboardInterrupt must propagate; we catch Exception, not BaseException."""
    from memeval.dreaming import redaction

    redact("warmup")
    plugins = redaction._get_plugins()
    bad = plugins[0]

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt("simulated")

    monkeypatch.setattr(bad, "analyze_line", interrupt)
    with pytest.raises(KeyboardInterrupt):
        redact("some text")


# --- G. network isolation ------------------------------------------------ #
def test_redact_makes_no_network_connect(monkeypatch):
    def _no_connect(self, *args, **kwargs):
        raise AssertionError(f"network connect attempted during redact: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", _no_connect)
    # Warm cache first, then real call.
    redact("warmup AKIAIOSFODNN7EXAMPLE")
    redact("another AKIAIOSFODNN7EXAMPLE")


# --- H. replacement-string contract -------------------------------------- #
def test_redaction_token_format():
    """Every replacement matches the [REDACTED:<type>] literal shape."""
    import re

    out = redact("key: AKIAIOSFODNN7EXAMPLE here")
    matches = re.findall(r"\[REDACTED:[^\]]+\]", out)
    assert matches, f"expected at least one REDACTED marker in {out!r}"


def test_redact_replaces_all_occurrences_on_one_line():
    line = "first AKIAIOSFODNN7EXAMPLE and second AKIAIOSFODNN7EXAMPLE"
    out = redact(line)
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert out.count("[REDACTED:") == 2


def test_redact_preserves_line_structure():
    src = "line one\nline two\nline three\n"
    out = redact(src)
    # Same number of newlines.
    assert out.count("\n") == src.count("\n")


def test_clean_line_is_returned_unchanged():
    src = "  spaces  and\ttabs preserved  \n"
    assert redact(src) == src


# --- I. driving-mechanism constraints (smoke-level) ---------------------- #
def test_filename_passed_to_plugins(monkeypatch):
    """analyze_line() must be called with filename='<daydream>'."""
    from memeval.dreaming import redaction

    redact("warmup")
    plugins = redaction._get_plugins()
    target = plugins[0]
    seen_filenames: list[str] = []
    original = target.analyze_line

    def record(filename, line, line_number):
        seen_filenames.append(filename)
        return original(filename=filename, line=line, line_number=line_number)

    monkeypatch.setattr(target, "analyze_line", record)
    redact("any text at all")
    assert seen_filenames, "analyze_line was not called"
    assert all(f == "<daydream>" for f in seen_filenames)


def test_plugin_instances_are_cached():
    """Plugin instances are built once and reused across redact() calls."""
    from memeval.dreaming import redaction

    redact("warmup")
    first = redaction._get_plugins()
    redact("another")
    second = redaction._get_plugins()
    assert first is second
