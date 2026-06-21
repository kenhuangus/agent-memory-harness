"""Per-custom-plugin tests for Daydream redaction (rubric §C + §O).

One section per plugin: class shape, secret_type, regex behavior on target
patterns, no false positives on prose. Six custom plugins total: Anthropic,
OpenRouter, GoogleCloud, BearerToken (ADR-005 §3) + DatabaseURL,
URLCredential (ADR-011 §1).
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "detect_secrets",
    reason="install with `pip install -e eval[daydream]` to run redaction tests",
)

from detect_secrets.plugins.base import RegexBasedDetector

from memeval.dreaming.redaction.plugins import (
    AnthropicKeyDetector,
    BearerTokenDetector,
    DatabaseURLDetector,
    GoogleCloudKeyDetector,
    OpenRouterKeyDetector,
    URLCredentialDetector,
)


def _findings(plugin, line: str) -> list:
    return list(plugin.analyze_line(filename="<test>", line=line, line_number=1))


# --- AnthropicKeyDetector ------------------------------------------------ #
def test_anthropic_plugin_class_shape():
    assert issubclass(AnthropicKeyDetector, RegexBasedDetector)
    assert AnthropicKeyDetector.secret_type == "Anthropic API Key"


def test_anthropic_plugin_matches_api03():
    key = "sk-ant-api03-" + "A" * 80
    assert _findings(AnthropicKeyDetector(), f"key: {key}")


def test_anthropic_plugin_matches_sid01():
    key = "sk-ant-sid01-" + "B" * 80
    assert _findings(AnthropicKeyDetector(), f"sid: {key}")


def test_anthropic_plugin_ignores_prose():
    assert not _findings(AnthropicKeyDetector(), "no anthropic key here, just words")


# --- OpenRouterKeyDetector ---------------------------------------------- #
def test_openrouter_plugin_class_shape():
    assert issubclass(OpenRouterKeyDetector, RegexBasedDetector)
    assert OpenRouterKeyDetector.secret_type == "OpenRouter API Key"


def test_openrouter_plugin_matches_v1_key():
    key = "sk-or-v1-" + "C" * 64
    assert _findings(OpenRouterKeyDetector(), f"key: {key}")


def test_openrouter_plugin_ignores_prose():
    assert not _findings(OpenRouterKeyDetector(), "openrouter is a service")


# --- GoogleCloudKeyDetector --------------------------------------------- #
def test_googlecloud_plugin_class_shape():
    assert issubclass(GoogleCloudKeyDetector, RegexBasedDetector)
    assert GoogleCloudKeyDetector.secret_type == "Google Cloud API Key"


def test_googlecloud_plugin_length_boundary():
    # 35-char tail = match. 34-char tail = no match.
    base = "AIza"
    assert _findings(GoogleCloudKeyDetector(), f"key: {base + 'a' * 35}")
    assert not _findings(GoogleCloudKeyDetector(), f"key: {base + 'a' * 34} (too short)")


def test_googlecloud_plugin_ignores_prose():
    assert not _findings(GoogleCloudKeyDetector(), "the google cloud platform docs say")


# --- BearerTokenDetector ----------------------------------------------- #
def test_bearer_plugin_class_shape():
    assert issubclass(BearerTokenDetector, RegexBasedDetector)
    assert BearerTokenDetector.secret_type == "Bearer Token"


def test_bearer_plugin_redacts_token_only():
    """Match span covers the token, not the literal word 'Bearer'."""
    token = "abcdef0123456789ABCDEF"
    line = f"Authorization: Bearer {token}"
    findings = _findings(BearerTokenDetector(), line)
    assert findings
    # secret_value should be the token, not "Bearer" or the prefix.
    values = {getattr(s, "secret_value", "") for s in findings}
    assert token in values
    for v in values:
        assert "Bearer" not in v


def test_bearer_plugin_ignores_prose():
    assert not _findings(BearerTokenDetector(), "we use bearer-token auth, see docs")


# --- DatabaseURLDetector (ADR-011) ------------------------------------- #
def test_database_url_plugin_class_shape():
    assert issubclass(DatabaseURLDetector, RegexBasedDetector)
    assert DatabaseURLDetector.secret_type == "Database Connection String"


def test_database_url_plugin_regex_verbatim():
    """Pin the regex per ADR-011 §Decision §1 — string-compare to the ADR."""
    expected = r"(postgres|postgresql|mysql|mongodb|redis|amqp)://[^:\s]+:[^@\s]+@"
    assert DatabaseURLDetector.denylist[0].pattern == expected


def test_database_url_plugin_matches_postgres():
    assert _findings(DatabaseURLDetector(), "DB: postgres://user:pw@host/db")


@pytest.mark.parametrize(
    "scheme", ["postgres", "postgresql", "mysql", "mongodb", "redis", "amqp"]
)
def test_database_url_plugin_matches_all_schemes(scheme):
    assert _findings(DatabaseURLDetector(), f"url: {scheme}://u:p@host/db")


def test_database_url_plugin_ignores_prose():
    assert not _findings(DatabaseURLDetector(), "the postgres database is fast")
    assert not _findings(DatabaseURLDetector(), "see redis://example for docs")


# --- URLCredentialDetector (ADR-011) ----------------------------------- #
def test_url_credential_plugin_class_shape():
    assert issubclass(URLCredentialDetector, RegexBasedDetector)
    assert URLCredentialDetector.secret_type == "URL-Embedded Credential"


def test_url_credential_plugin_regex_verbatim():
    expected = r"[?&](access_token|api_key|auth|token|secret|password)=[^&\s]{6,}"
    assert URLCredentialDetector.denylist[0].pattern == expected


@pytest.mark.parametrize(
    "key", ["access_token", "api_key", "auth", "token", "secret", "password"]
)
def test_url_credential_plugin_matches_all_keys(key):
    assert _findings(URLCredentialDetector(), f"url: https://x?{key}=abcdef123")


def test_url_credential_plugin_length_boundary():
    # 6-char value = match.
    assert _findings(URLCredentialDetector(), "url: https://x?token=abcdef")
    # 5-char value = no match (regex requires {6,}).
    assert not _findings(URLCredentialDetector(), "url: https://x?token=abcde")


def test_url_credential_plugin_ignores_prose():
    assert not _findings(URLCredentialDetector(), "use the api_key argument")


# --- Custom-plugin sweep: all 6 ignore plausible prose ------------------ #
@pytest.mark.parametrize(
    "plugin_cls",
    [
        AnthropicKeyDetector,
        OpenRouterKeyDetector,
        GoogleCloudKeyDetector,
        BearerTokenDetector,
        DatabaseURLDetector,
        URLCredentialDetector,
    ],
)
def test_custom_plugins_ignore_prose(plugin_cls):
    plugin = plugin_cls()
    assert not _findings(plugin, "normal sentence, no keys here")
    assert not _findings(plugin, "User pasted their AWS access key in chat.")
