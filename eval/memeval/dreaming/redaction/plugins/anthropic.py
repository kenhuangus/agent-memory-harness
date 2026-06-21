"""Anthropic API key detector (sk-ant-api03 / sk-ant-sid01) — ADR-005 §3."""

from __future__ import annotations

import re

from detect_secrets.plugins.base import RegexBasedDetector


class AnthropicKeyDetector(RegexBasedDetector):
    """Detect Anthropic API keys (``sk-ant-api03-...`` and ``sk-ant-sid01-...``)."""

    secret_type = "Anthropic API Key"
    denylist = [re.compile(r"sk-ant-(?:api03|sid01)-[A-Za-z0-9_\-]{40,}")]
