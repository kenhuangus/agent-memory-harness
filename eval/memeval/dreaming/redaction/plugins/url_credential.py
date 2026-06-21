"""URL query-string credential detector (?access_token=..., ?api_key=...) — ADR-011 §1.

Regex is verbatim from ADR-dreaming-011 §Decision §1.
"""

from __future__ import annotations

import re

from detect_secrets.plugins.base import RegexBasedDetector


class URLCredentialDetector(RegexBasedDetector):
    """Detect ``?<key>=<value>`` URL-embedded credentials (6+ char values)."""

    secret_type = "URL-Embedded Credential"
    denylist = [re.compile(r"[?&](access_token|api_key|auth|token|secret|password)=[^&\s]{6,}")]
