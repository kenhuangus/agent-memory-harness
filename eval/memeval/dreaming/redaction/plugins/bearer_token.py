"""Authorization: Bearer <token> header detector — ADR-005 §3.

Uses a lookbehind so the matched span covers only the token, not the
literal word "Bearer" — per the rubric: the redacted span must cover the
token, not the keyword.
"""

from __future__ import annotations

import re

from detect_secrets.plugins.base import RegexBasedDetector


class BearerTokenDetector(RegexBasedDetector):
    """Detect tokens after ``Authorization: Bearer ``."""

    secret_type = "Bearer Token"
    # (?<=...) lookbehind: only the token after "Bearer " is the match span.
    denylist = [re.compile(r"(?<=[Bb]earer )[A-Za-z0-9._\-]{16,}")]
