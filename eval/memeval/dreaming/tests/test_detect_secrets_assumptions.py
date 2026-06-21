"""Pins the detect-secrets v1.5.0 behaviors ADR-dreaming-005 depends on.

Spike (2026-06-20) found that:
  1. structured pattern detectors work via plugin.analyze_line()
  2. entropy detectors are unusable on prose
  3. transient_settings + path-based custom plugin loading is broken
  4. a custom RegexBasedDetector subclass plugged into analyze_line() works
  5. no network connect() happens during a scan

If a future detect-secrets upgrade silently changes (1), (4), or (5), these
tests fail loudly so we re-validate before upgrading the pin. (2) is asserted
as documented-limitation so a future upstream fix becomes visible.
"""
from __future__ import annotations

import re
import socket

import pytest

pytest.importorskip(
    "detect_secrets",
    reason="install with `pip install -e eval[daydream]` to run redaction tests",
)

from detect_secrets.plugins.aws import AWSKeyDetector
from detect_secrets.plugins.base import RegexBasedDetector
from detect_secrets.plugins.github_token import GitHubTokenDetector
from detect_secrets.plugins.high_entropy_strings import Base64HighEntropyString
from detect_secrets.plugins.jwt import JwtTokenDetector


def _findings(plugin, line: str) -> list:
    """Drive a plugin instance directly — the API shape ADR-dreaming-005 uses."""
    return list(plugin.analyze_line(filename="<test>", line=line, line_number=1))


# ---- (1) structured detectors catch their targets, ignore prose ----------- #
def test_aws_key_detector_catches_aws_key_and_ignores_prose():
    secrets = _findings(AWSKeyDetector(), "AWS key AKIAIOSFODNN7EXAMPLE in prose.")
    assert any(s.secret_value == "AKIAIOSFODNN7EXAMPLE" for s in secrets)
    assert not _findings(AWSKeyDetector(), "Just normal English about Python.")


def test_github_token_detector_fires_on_ghp_line():
    """Contract we depend on: detector flags a GitHub-Token finding on this line.

    In v1.5.0, `secret_value` is normalized to the keyword prefix (`'ghp'`)
    rather than the full token — so we assert on `type`, not value content.
    The full-token span lives in the original `line` and is what `redact()`
    replaces.
    """
    line = "token=ghp_16C7e42F292c6912E7710c838347Ae178B4a in tool output"
    secrets = _findings(GitHubTokenDetector(), line)
    assert any(s.type == "GitHub Token" for s in secrets), (
        f"GitHubTokenDetector did not fire on a clear ghp_ token line: {secrets!r}"
    )


def test_jwt_detector_catches_jwt():
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    assert any(jwt.startswith(s.secret_value[:20]) for s in _findings(JwtTokenDetector(), jwt))


# ---- (4) custom RegexBasedDetector subclass works via analyze_line() ----- #
class _AnthropicKeyDetector(RegexBasedDetector):
    """Mirrors what eval/memeval/dreaming/redaction/plugins/anthropic.py will be."""

    secret_type = "Anthropic API Key"
    denylist = [re.compile(r"sk-ant-(?:api03|sid01)-[A-Za-z0-9_\-]{40,}")]


def test_custom_anthropic_key_detector_catches_sk_ant_key_and_ignores_prose():
    key = "sk-ant-api03-" + "A" * 80
    secrets = _findings(_AnthropicKeyDetector(), f"user shared {key} in chat")
    assert any(s.secret_value == key for s in secrets)
    assert not _findings(_AnthropicKeyDetector(), "normal sentence with no keys")


def test_potential_secret_shape_unchanged():
    """The fields ADR-dreaming-005 relies on must keep existing."""
    [s] = _findings(AWSKeyDetector(), "AKIAIOSFODNN7EXAMPLE")
    assert isinstance(s.type, str) and s.type
    assert isinstance(s.secret_value, str) and s.secret_value
    assert s.line_number == 1
    assert hasattr(s, "filename")


# ---- (5) no network connect during a scan -------------------------------- #
def test_scan_makes_no_network_connect(monkeypatch):
    def _no_connect(self, *a, **kw):
        raise AssertionError(f"network connect attempted during scan: {a!r}")

    monkeypatch.setattr(socket.socket, "connect", _no_connect)
    _findings(AWSKeyDetector(), "AKIAIOSFODNN7EXAMPLE")
    _findings(GitHubTokenDetector(), "ghp_16C7e42F292c6912E7710c838347Ae178B4a")
    _findings(_AnthropicKeyDetector(), "sk-ant-api03-" + "A" * 80)


# ---- (2) entropy detector via analyze_line() is clean on prose ----------- #
def test_entropy_detector_via_analyze_line_silent_on_prose():
    """Pins a non-obvious v1.5.0 behavior found post-spike.

    The original 2026-06-20 spike saw `Base64HighEntropyString` flag every
    English word ≥4 chars — but that was via `scan.scan_line(line)` with
    `default_settings()` context. When the same detector is driven directly
    via `analyze_line()` (the API ADR-dreaming-005 uses), it returns zero
    findings on the same prose. The "broken on prose" property appears to be
    a `scan_line+settings` chain effect, NOT inherent to the detector class.

    If this test STARTS failing — i.e., direct-drive entropy detection
    becomes noisy on prose — investigate before relying on it. If it KEEPS
    passing across several upgrades, that's evidence to revisit the
    "no entropy detectors in v1" decision in ADR-dreaming-005.
    """
    detector = Base64HighEntropyString(limit=4.5)  # default threshold
    findings = _findings(detector, "User pasted their AWS access key in chat.")
    assert findings == [], (
        f"Base64HighEntropyString via analyze_line() now flags prose: "
        f"{[s.secret_value for s in findings]!r}. Revisit ADR-dreaming-005 "
        f"to see if entropy detectors can join the v1 plugin set."
    )
