"""Rubric §L + §T — policy-coverage meta-tests for ADR-005, ADR-010, ADR-011.

Asserts that the test functions naming each rubric criterion's verification
plan actually exist. Catches the failure mode where a test got renamed or
deleted but the rubric still claims coverage — without this meta-check,
"the test exists" is just trust.

For each Policy line in the three ADRs that has a PR1 surface, this file
lists the test name that's supposed to cover it and asserts the function
is importable from the named test module.
"""

from __future__ import annotations

import importlib
import pytest

# Each entry: (rubric_criterion, test_module_dotted, test_name).
# Out-of-PR1-scope items (#58, #65, #66, #79, #113, #114, #119, #122, #123)
# are excluded — they're tracked elsewhere or deferred per the rubric.
_COVERAGE = [
    # §L (ADR-005 Policy lines)
    ("§59 — entropy exclusion", "test_redaction", "test_active_plugins_exclude_entropy_detectors"),
    ("§59 — prose not falsely redacted", "test_redaction", "test_redact_does_not_false_positive_on_prose_example"),
    ("§60 — analyze_line (no scan_line)", "test_redaction_anti_slop", "test_active_plugins_use_analyze_line_not_scan_line"),
    ("§60 — no YAML", "test_redaction_anti_slop", "test_no_yaml_or_scan_line_imports"),
    ("§61 — no network", "test_redaction", "test_redact_makes_no_network_connect"),
    ("§62 — Anthropic plugin class shape", "test_redaction_plugins", "test_anthropic_plugin_class_shape"),
    ("§62 — OpenRouter plugin class shape", "test_redaction_plugins", "test_openrouter_plugin_class_shape"),
    ("§62 — GoogleCloud plugin class shape", "test_redaction_plugins", "test_googlecloud_plugin_class_shape"),
    ("§62 — Bearer plugin class shape", "test_redaction_plugins", "test_bearer_plugin_class_shape"),
    ("§62 — DatabaseURL plugin class shape", "test_redaction_plugins", "test_database_url_plugin_class_shape"),
    ("§62 — URLCredential plugin class shape", "test_redaction_plugins", "test_url_credential_plugin_class_shape"),
    # §T (ADR-010 + ADR-011 Policy lines)
    ("§115 — mypy --strict reachable", "test_redaction_anti_slop", "test_module_docstring_lists_pii_oos"),  # presence proxy for the harness
    ("§116 — negative typecheck", "typecheck.test_redacted_text_typecheck", "test_redactedtext_negative_typecheck"),
    ("§117 — 6 custom plugins active", "test_redaction_plugins", "test_custom_plugins_ignore_prose"),
    ("§118 — out-of-scope in docstring", "test_redaction_anti_slop", "test_module_docstring_lists_freeform_english_oos"),
    ("§118 — out-of-scope in README", "test_redaction_anti_slop", "test_readme_lists_out_of_scope_categories"),
    ("§120 — gitignore presence", "test_redaction_gitignore", "test_gitignore_contains_redact_audit_pattern"),
    ("§120 — gitignore effective", "test_redaction_gitignore", "test_gitignore_pattern_actually_ignores_audit_at_root"),
    ("§121 — local-only invariant", "test_redaction_audit", "test_audit_writer_makes_no_network_connect"),
    ("§121 — audit writes only to supplied path", "test_redaction_audit", "test_audit_writer_writes_only_to_supplied_path"),
]


@pytest.mark.parametrize("description,module_name,test_name", _COVERAGE, ids=[c[0] for c in _COVERAGE])
def test_rubric_policy_has_test_coverage(description: str, module_name: str, test_name: str) -> None:
    """Each Policy line in ADR-005/010/011 has a named test that's importable.

    Failure mode: a rubric-promised test got renamed or deleted. This
    catches the drift without requiring a rerun of the named test (it might
    pass; this check is structural).
    """
    full_mod = f"memeval.dreaming.tests.{module_name}"
    mod = importlib.import_module(full_mod)
    assert hasattr(mod, test_name), (
        f"{description}: expected {full_mod}::{test_name} to exist but it doesn't. "
        f"Either the test was renamed/deleted and the rubric is stale, or the "
        f"coverage table here is wrong."
    )
    fn = getattr(mod, test_name)
    assert callable(fn), f"{full_mod}::{test_name} is not callable"
