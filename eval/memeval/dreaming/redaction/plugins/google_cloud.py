"""Google Cloud / Google API key detector (AIza...) — ADR-005 §3."""

from __future__ import annotations

import re

from detect_secrets.plugins.base import RegexBasedDetector


class GoogleCloudKeyDetector(RegexBasedDetector):
    """Detect Google API keys (``AIza`` followed by 35 url-safe chars)."""

    secret_type = "Google Cloud API Key"
    # Boundary anchors prevent matching shorter / longer tails.
    denylist = [re.compile(r"(?<![A-Za-z0-9_\-])AIza[0-9A-Za-z\-_]{35}(?![A-Za-z0-9_\-])")]
