"""Daydream v1 secret redaction — owner: **Scott B.** (@NerdAlert58).

Inline secret-redaction layer applied to every string Daydream passes to
``LLMClient.complete()``. v1 reads Claude Code session JSONL directly per
ADR-dreaming-005 (the multi-harness adapter from ADR-harness-005 ships
later).

Layered policy
--------------
1. ``detect-secrets`` v1.5.0 structured detectors (AWS, Azure, GitHub, GitLab,
   Slack, Stripe, OpenAI, JWT, PrivateKey, BasicAuth, Artifactory) driven
   directly via ``plugin.analyze_line()`` — no ``scan_line``, no
   ``transient_settings``, no YAML config.
2. Six Daydream-local custom plugins under ``plugins/`` covering Anthropic,
   OpenRouter, Google Cloud, Bearer-token headers (ADR-005 §Decision §3) plus
   Database connection strings and URL-embedded credentials (ADR-011 §1).
3. Entropy detectors (``Base64HighEntropyString``, ``HexHighEntropyString``)
   are explicitly **excluded** — the 2026-06-20 spike proved them unfit for
   prose under ``default_settings``.

Structural contract
-------------------
``redact(text: str) -> RedactedText`` is the **only** producer of
``RedactedText`` (ADR-dreaming-010). The ``LLMClient.complete()`` Protocol
(landing in PR2) will only accept ``RedactedText`` — mypy ``--strict``
enforces the trust boundary instead of caller discipline.

Out of scope (v1)
-----------------
This redaction layer does **not** catch:

* **Free-form English credentials** ("my password is hunter2", "the API key
  is X"). No pattern detector — would require LLM-based detection, which
  contradicts "redact before LLM call."
* **Novel/custom token formats** (one-off MCP server tokens, experimental
  provider keys). Surface these via the FP/FN audit file
  (``_write_audit_record``) and add detectors in successor ADRs when patterns
  repeat.
* **PII** (personal names, emails, addresses). Separate concern; deferred.
  See ``docs/honcho-comparison.md`` (locally on ``honcho-research`` branch)
  for the Presidio path if/when PII becomes load-bearing.

This list is the contract with downstream users: if your sessions contain
these, layer your own controls — Daydream redaction will not catch them.

Other contracts
---------------
* Lazy import: ``detect_secrets`` and the plugin classes are imported INSIDE
  ``redact()`` (architecture.md §3 — offline path stays stdlib-only at import).
* Fail-open: ``redact()`` swallows per-plugin failures, logs, and continues.
  ``KeyboardInterrupt`` / ``SystemExit`` are NOT swallowed.
* Network: zero network connections during a scan
  (detect-secrets ``--only-verified`` family is unreachable from this code path).
* Events: per-detector emissions go through the ``emit()`` shim from
  ``memeval.dreaming.events`` once that module exists (ADR-009; PR3+).
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, NewType

if TYPE_CHECKING:  # pragma: no cover
    pass

#: Structural marker that a string has been routed through ``redact()``.
#: ``LLMClient.complete()`` (PR2) accepts only ``RedactedText``; mypy
#: ``--strict`` enforces it. At runtime, this is a plain ``str``.
RedactedText = NewType("RedactedText", str)

__all__ = ["RedactedText", "redact"]

_logger = logging.getLogger(__name__)
_plugins_lock = threading.Lock()
_plugins_cache: list[Any] | None = None
_REDACTION_FILENAME = "<daydream>"


def _build_plugins() -> list[Any]:
    """Lazy-build the curated plugin list. Called once per process from ``redact()``.

    All ``detect_secrets`` and custom-plugin imports happen INSIDE this
    function so ``import memeval.dreaming.redaction`` is free of network/
    third-party load (architecture.md §3).
    """
    try:
        from detect_secrets.plugins.artifactory import ArtifactoryDetector
        from detect_secrets.plugins.aws import AWSKeyDetector
        from detect_secrets.plugins.azure_storage_key import AzureStorageKeyDetector
        from detect_secrets.plugins.basic_auth import BasicAuthDetector
        from detect_secrets.plugins.github_token import GitHubTokenDetector
        from detect_secrets.plugins.gitlab_token import GitLabTokenDetector
        from detect_secrets.plugins.jwt import JwtTokenDetector
        from detect_secrets.plugins.openai import OpenAIDetector
        from detect_secrets.plugins.private_key import PrivateKeyDetector
        from detect_secrets.plugins.slack import SlackDetector
        from detect_secrets.plugins.stripe import StripeDetector
    except ImportError as exc:
        raise ImportError(
            "detect-secrets is required for Daydream redaction. "
            "Install with: pip install 'agent-memory-eval[daydream]'"
        ) from exc

    from .plugins.anthropic import AnthropicKeyDetector
    from .plugins.bearer_token import BearerTokenDetector
    from .plugins.database_url import DatabaseURLDetector
    from .plugins.google_cloud import GoogleCloudKeyDetector
    from .plugins.openrouter import OpenRouterKeyDetector
    from .plugins.url_credential import URLCredentialDetector

    # 11 structured detect-secrets plugins (ADR-005 §Decision §1) + 6 custom
    # plugins (ADR-005 §3 + ADR-011 §1). Combined in one tuple so mypy doesn't
    # infer a narrower type from the first loop.
    all_classes: tuple[type, ...] = (
        AWSKeyDetector,
        AzureStorageKeyDetector,
        GitHubTokenDetector,
        GitLabTokenDetector,
        SlackDetector,
        StripeDetector,
        OpenAIDetector,
        JwtTokenDetector,
        PrivateKeyDetector,
        BasicAuthDetector,
        ArtifactoryDetector,
        AnthropicKeyDetector,
        OpenRouterKeyDetector,
        GoogleCloudKeyDetector,
        BearerTokenDetector,
        DatabaseURLDetector,
        URLCredentialDetector,
    )
    plugins: list[Any] = []
    for plugin_cls in all_classes:
        try:
            plugins.append(plugin_cls())
        except Exception as exc:  # one bad plugin must not kill the whole list
            _logger.warning(
                "Could not instantiate %s: %s; skipping",
                plugin_cls.__name__,
                exc,
            )
    return plugins


def _get_plugins() -> list[Any]:
    """Return the cached plugin list, building it on first call (thread-safe)."""
    global _plugins_cache
    if _plugins_cache is not None:
        return _plugins_cache
    with _plugins_lock:
        if _plugins_cache is None:
            _plugins_cache = _build_plugins()
        return _plugins_cache


def redact(text: str) -> RedactedText:
    """Redact secrets from ``text`` and return a ``RedactedText`` wrapper.

    For each line, drives every plugin in the curated module-level list via
    ``analyze_line(filename="<daydream>", line=..., line_number=...)`` and
    replaces every detected ``secret_value`` span with
    ``[REDACTED:<secret_type>]``. Preserves line structure verbatim.

    Fail-open: per-plugin exceptions are logged (WARNING) and skipped. The
    function never raises for any ``str`` input — except ``ImportError`` at
    first call when ``detect-secrets`` is not installed (loud rather than
    silently shipping unredacted prompts).
    """
    if not text:
        return RedactedText("")

    plugins = _get_plugins()
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    for lineno, line in enumerate(lines, start=1):
        redacted = line
        for plugin in plugins:
            try:
                findings = list(
                    plugin.analyze_line(
                        filename=_REDACTION_FILENAME,
                        line=line,
                        line_number=lineno,
                    )
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                _logger.warning(
                    "Plugin %s raised %s on line %d; skipping",
                    plugin.__class__.__name__,
                    type(exc).__name__,
                    lineno,
                )
                continue
            for secret in findings:
                value = getattr(secret, "secret_value", None)
                stype = getattr(secret, "type", None) or plugin.__class__.__name__
                if not value:
                    continue
                marker = f"[REDACTED:{stype}]"
                redacted = redacted.replace(value, marker)
        out.append(redacted)
    return RedactedText("".join(out))
