"""OpenRouter API key detector (sk-or-v1) — ADR-005 §3."""

from __future__ import annotations

import re

from detect_secrets.plugins.base import RegexBasedDetector


class OpenRouterKeyDetector(RegexBasedDetector):
    """Detect OpenRouter API keys (``sk-or-v1-...``)."""

    secret_type = "OpenRouter API Key"
    denylist = [re.compile(r"sk-or-v1-[A-Za-z0-9]{32,}")]
