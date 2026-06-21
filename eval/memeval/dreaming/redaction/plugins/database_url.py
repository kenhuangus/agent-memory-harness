"""Database connection string detector (postgres / mysql / mongodb / redis / amqp) — ADR-011 §1.

Regex is verbatim from ADR-dreaming-011 §Decision §1.
"""

from __future__ import annotations

import re

from detect_secrets.plugins.base import RegexBasedDetector


class DatabaseURLDetector(RegexBasedDetector):
    """Detect database URLs of the form ``<scheme>://user:password@host/...``."""

    secret_type = "Database Connection String"
    denylist = [re.compile(r"(postgres|postgresql|mysql|mongodb|redis|amqp)://[^:\s]+:[^@\s]+@")]
