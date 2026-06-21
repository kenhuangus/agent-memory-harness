"""Daydream-local custom secret detectors (ADR-dreaming-005 §3 + ADR-011 §1).

Each plugin inherits from ``detect_secrets.plugins.base.RegexBasedDetector``
and ships as a single class so per-plugin upstream PRs to ``Yelp/detect-secrets``
are clean diffs after v1 settles.
"""

from .anthropic import AnthropicKeyDetector
from .bearer_token import BearerTokenDetector
from .database_url import DatabaseURLDetector
from .google_cloud import GoogleCloudKeyDetector
from .openrouter import OpenRouterKeyDetector
from .url_credential import URLCredentialDetector

__all__ = [
    "AnthropicKeyDetector",
    "BearerTokenDetector",
    "DatabaseURLDetector",
    "GoogleCloudKeyDetector",
    "OpenRouterKeyDetector",
    "URLCredentialDetector",
]
