# PR1 — Redaction module — done rubric

Implementer checks each item off before marking PR ready for review. Jasnah re-verifies on review. Anything fuzzy or unchecked = NOT DONE. Each criterion is boolean (PASS / FAIL / N-A). No partial credit. No compounds — every "and" is split into its own line.

Scope: PR1 only. Module at `eval/memeval/dreaming/redaction/` exposing `redact(text: str) -> RedactedText` plus six custom detect-secrets plugins, the `RedactedText` NewType, and the per-chunk audit-file writer. NOT the Daydream engine, NOT the CLI. LLMClient signature update (ADR-010) lands in PR2 — PR1 only defines the NewType so PR2 can wire it.

Assumption: `eval/memeval/dreaming/tests/test_detect_secrets_assumptions.py` is green at PR1 merge. Any of those failing red-pages this entire rubric.

Anchors:
- ADR-005 = `docs/adrs/ADR-dreaming-005-v1-inline-redaction.md`
- ADR-006 = `docs/adrs/ADR-dreaming-006-llmclient-completion-dataclass.md`
- ADR-009 = `docs/adrs/ADR-dreaming-009-events-shim.md`
- ADR-010 = `docs/adrs/ADR-dreaming-010-redactedtext-newtype.md`
- ADR-011 = `docs/adrs/ADR-dreaming-011-expanded-redaction-scope.md`
- arch §3 = `architecture.md` §3 (offline-imports rule)

Per-PR judgment calls (recorded once, not re-litigated per criterion):
- **mypy --strict CI step**: ADR-010 lists this as an Open item with "coordinate with Ken." Treated as IN-SCOPE for PR1 — the negative-typecheck test in §N requires mypy to actually run somewhere reproducible. If Ken's CI lane isn't ready, the implementer ships a `make typecheck` target invoked by the test harness; criterion 73 covers this.
- **LLMClient signature update (ADR-010)**: explicitly PR2 scope (no LLMClient code in PR1 per criterion 58). PR1 ships the `RedactedText` NewType only; PR2 wires `LLMClient.complete()` to consume it.
- **Audit-file path resolution**: ADR-011 uses `${MEMORY_STORE%/*}/dream/<session_id>.redact-audit.jsonl` and references ADR-harness-004 for resolution. Treated as a writer-side dependency in PR1: PR1 ships the writer with a `path` argument the caller supplies; resolution rules from ADR-harness-004 are PR2/PR3 wiring concern. Criterion 87 makes this explicit.

---

## A. Module shape & public surface

- [ ] 1. `eval/memeval/dreaming/redaction/__init__.py` exists and re-exports `redact` — ADR-005 §Consequences "Contract — source of truth" — verified by `from memeval.dreaming.redaction import redact` succeeding in `test_public_surface_import`.
- [ ] 2. `redact` signature is exactly `redact(text: str) -> RedactedText` (no extra params, no kwargs, no `Optional`) — ADR-005 §Consequences "Shape" + ADR-010 §Shape — verified by `inspect.signature` assertion in `test_redact_signature_is_frozen`; return annotation must be `RedactedText`, not `str`.
- [ ] 3. Calling `redact("")` returns a `RedactedText` value whose underlying str is `""` (empty input is a no-op, not an error) — ADR-005 §Consequences (fail-open posture) — verified by `test_redact_empty_string_returns_empty`.
- [ ] 4. `redact(text)` returns a `RedactedText` (which IS a `str` at runtime) for every input that's a `str` (never `None`, never `bytes`, never a list) — ADR-005 §Consequences "Shape" + ADR-010 — verified by `test_redact_return_type_is_str` (asserts `isinstance(result, str)` since NewType is a `str` at runtime).

## B. Curated structured plugin coverage (the 11 from ADR-005 §Decision §1)

Each plugin gets its own line — no compounding. Each is verified by feeding `redact()` a line containing a synthetic secret of that type and asserting the returned string contains `[REDACTED:<expected_secret_type>]` and no longer contains the synthetic secret.

- [ ] 5. `AWSKeyDetector` is in the active plugin list and fires — ADR-005 §Decision §1 — `test_redact_replaces_aws_key`.
- [ ] 6. `AzureStorageKeyDetector` is in the active plugin list and fires — ADR-005 §Decision §1 — `test_redact_replaces_azure_storage_key`.
- [ ] 7. `GitHubTokenDetector` is in the active plugin list and fires — ADR-005 §Decision §1 — `test_redact_replaces_github_token`.
- [ ] 8. `GitLabTokenDetector` is in the active plugin list and fires — ADR-005 §Decision §1 — `test_redact_replaces_gitlab_token`.
- [ ] 9. `SlackDetector` is in the active plugin list and fires — ADR-005 §Decision §1 — `test_redact_replaces_slack_token`.
- [ ] 10. `StripeDetector` is in the active plugin list and fires — ADR-005 §Decision §1 — `test_redact_replaces_stripe_key`.
- [ ] 11. `OpenAIDetector` is in the active plugin list and fires — ADR-005 §Decision §1 — `test_redact_replaces_openai_key`.
- [ ] 12. `JwtTokenDetector` is in the active plugin list and fires — ADR-005 §Decision §1 — `test_redact_replaces_jwt`.
- [ ] 13. `PrivateKeyDetector` is in the active plugin list and fires — ADR-005 §Decision §1 — `test_redact_replaces_private_key`.
- [ ] 14. `BasicAuthDetector` is in the active plugin list and fires — ADR-005 §Decision §1 — `test_redact_replaces_basic_auth`.
- [ ] 15. `ArtifactoryDetector` is in the active plugin list and fires — ADR-005 §Decision §1 — `test_redact_replaces_artifactory_token`.

## C. Custom plugins (originally 4 from ADR-005 §Decision §3; expanded to 6 by ADR-011 §Decision §1)

Each lives at `eval/memeval/dreaming/redaction/plugins/<name>.py` and inherits from `detect_secrets.plugins.base.RegexBasedDetector`.

- [ ] 16. `AnthropicKeyDetector` exists, inherits `RegexBasedDetector`, sets `secret_type = "Anthropic API Key"` — ADR-005 §Decision §3 — `test_anthropic_plugin_class_shape`.
- [ ] 17. `AnthropicKeyDetector.denylist` matches `sk-ant-api03-<40+ token chars>` — ADR-005 §Decision §3 — `test_anthropic_plugin_matches_api03`.
- [ ] 18. `AnthropicKeyDetector.denylist` matches `sk-ant-sid01-<40+ token chars>` — ADR-005 §Decision §3 — `test_anthropic_plugin_matches_sid01`.
- [ ] 19. `OpenRouterKeyDetector` exists, inherits `RegexBasedDetector`, sets a distinct `secret_type` — ADR-005 §Decision §3 — `test_openrouter_plugin_class_shape`.
- [ ] 20. `OpenRouterKeyDetector.denylist` matches `sk-or-v1-<token chars>` — ADR-005 §Decision §3 — `test_openrouter_plugin_matches_v1_key`.
- [ ] 21. `GoogleCloudKeyDetector` exists, inherits `RegexBasedDetector`, sets a distinct `secret_type` — ADR-005 §Decision §3 — `test_googlecloud_plugin_class_shape`.
- [ ] 22. `GoogleCloudKeyDetector.denylist` matches `AIza[0-9A-Za-z\-_]{35}` exactly (length boundary: 35-char tail matches; 34-char tail does not) — ADR-005 §Decision §3 — `test_googlecloud_plugin_length_boundary`.
- [ ] 23. `BearerTokenDetector` exists, inherits `RegexBasedDetector`, sets a distinct `secret_type` — ADR-005 §Decision §3 — `test_bearer_plugin_class_shape`.
- [ ] 24. `BearerTokenDetector.denylist` matches `Authorization: Bearer <token>` headers and the redacted span covers the token (not the literal word "Bearer") — ADR-005 §Decision §3 — `test_bearer_plugin_redacts_token_only`.
- [ ] 25. All 6 custom plugins ignore plausible prose ("normal sentence, no keys here") — ADR-005 §Rationale (low FP requirement) + ADR-011 — `test_custom_plugins_ignore_prose` (parametrized over all 6 — including the two new ADR-011 plugins).

## D. Entropy-detector exclusion (ADR-005 §Decision §2)

- [ ] 26. `Base64HighEntropyString` does NOT appear in the active plugin list — ADR-005 §Decision §2 — `test_active_plugins_exclude_base64_entropy` (introspects the curated module-level plugin list).
- [ ] 27. `HexHighEntropyString` does NOT appear in the active plugin list — ADR-005 §Decision §2 — `test_active_plugins_exclude_hex_entropy`.
- [ ] 28. `redact("User pasted their AWS access key in chat.")` returns the input UNCHANGED (the documented prose example must not be falsely redacted by any plugin in the curated set) — ADR-005 §Decision §2 + §Tradeoffs — `test_redact_does_not_false_positive_on_prose_example`.

## E. Lazy-import rule (architecture §3 + ADR-005 §Tradeoffs "Dependency footprint")

- [ ] 29. `import memeval.dreaming.redaction` succeeds when `detect_secrets` is NOT installed (lazy import means the package itself imports cleanly) — arch §3 + ADR-005 §Tradeoffs — `test_module_imports_without_detect_secrets` (monkeypatches `sys.modules` to make `detect_secrets` unimportable BEFORE the redaction package is imported in a fresh subprocess).
- [ ] 30. `detect_secrets` is NOT in `sys.modules` after `import memeval.dreaming.redaction` — arch §3 — `test_redaction_import_does_not_load_detect_secrets` (clean subprocess + assertion).
- [ ] 31. `detect_secrets` IS in `sys.modules` after one call to `redact("x")` — confirms the lazy import actually fires on use — arch §3 — `test_redact_call_triggers_detect_secrets_import`.
- [ ] 32. Plugin class imports (`from detect_secrets.plugins.aws import AWSKeyDetector`, etc.) also happen inside `redact()` (not at module top of `redaction/__init__.py` or `redaction/_core.py`) — arch §3 strict reading — `test_no_detect_secrets_plugin_imports_at_module_top` (AST-scans the source files).

## F. Fail-open behavior (ADR-005 §Consequences "fail-open" posture + the 3 failure modes the dispatcher named)

- [ ] 33. `redact()` never raises for any `str` input (parametrized over: empty, single char, 10 MB string, multi-line with `\r\n`, multi-line with bare `\n`, unicode incl. emoji, NUL byte `"\x00"`) — ADR-005 §Consequences — `test_redact_never_raises` (parametrized).
- [ ] 34. If ONE plugin class fails to instantiate (e.g. ImportError simulated via monkeypatch), `redact()` still runs the surviving plugins and returns a string — ADR-005 fail-open — `test_one_plugin_instantiation_failure_does_not_kill_redact` (monkeypatch one plugin class to raise on `__init__`; assert another plugin's secret type still gets redacted in the output).
- [ ] 35. If ONE plugin's `analyze_line()` raises mid-line, `redact()` catches the exception, logs it (via `logging` module — not print), and continues with the remaining plugins — ADR-005 fail-open — `test_analyze_line_exception_is_logged_and_skipped` (uses `caplog`; asserts WARNING-or-higher record with the failing plugin's class name, and asserts another plugin's redaction still occurs in the same `redact()` call).
- [ ] 36. The exception-handler in §35 is SCOPED — it catches `Exception`, not bare `except:` (no swallowing `KeyboardInterrupt`/`SystemExit`) — slop-detection — `test_redact_does_not_swallow_keyboardinterrupt` (monkeypatch a plugin to raise `KeyboardInterrupt`; assert it propagates).
- [ ] 37. If `detect_secrets` is uninstallable at call time (importable path missing), `redact()` raises a clear `ImportError` with a message naming the `daydream` extra — ADR-005 §Tradeoffs (the caller import path must be graceful, not silently no-op) — `test_redact_raises_clear_importerror_when_detect_secrets_missing`. NOTE: this is a deliberate exception to §33 — better to fail loudly than silently no-op redaction. Implementer: if you read ADR-005 as preferring silent no-op + WARNING log instead, flag at review and we adjust.

## G. Network isolation (ADR-005 §Consequences "no network verification")

- [ ] 38. `redact()` makes zero network connections during a scan — ADR-005 §Consequences — `test_redact_makes_no_network_connect` (monkeypatches `socket.socket.connect` to raise; mirrors the existing assumption test pattern).

## H. Replacement-string contract

- [ ] 39. Every redaction replaces the detected `secret_value` span with the exact literal `[REDACTED:<secret_type>]` — ADR-005 §Consequences "Shape" — `test_redaction_token_format` (regex assertion on output: `\[REDACTED:[^\]]+\]`).
- [ ] 40. `<secret_type>` in the replacement is the detector's `.secret_type` attribute verbatim (e.g. `"AWS Access Key"`, not lowercased / not slugified) — ADR-005 §Consequences "Shape" — `test_redaction_token_uses_secret_type_verbatim`.
- [ ] 41. Multiple secrets of the same type on one line are all replaced (not just the first) — ADR-005 §Consequences "Shape" ("each detected `secret_value` span") — `test_redact_replaces_all_occurrences_on_one_line`.
- [ ] 42. Multiple secrets of DIFFERENT types on one line are all replaced, each with its own marker — ADR-005 §Consequences — `test_redact_replaces_mixed_types_on_one_line`.
- [ ] 43. Multi-line input: each line is processed independently and the line structure (newlines) is preserved in the output — ADR-005 §Consequences "Shape" ("For each line") — `test_redact_preserves_line_structure`.
- [ ] 44. A line with no secrets is returned byte-identical (no whitespace normalization, no trailing-newline stripping, no re-encoding) — slop-detection — `test_clean_line_is_returned_unchanged`.

## I. Driving-mechanism constraints (ADR-005 §Consequences "Policy" lines)

- [ ] 45. `redact()` calls `plugin.analyze_line(filename=..., line=..., line_number=...)` — never `scan_line()`, never `scan.scan()` — ADR-005 §Consequences "no `scan_line()`" — `test_redact_uses_analyze_line_not_scan_line` (AST-scan of source: `analyze_line` appears, `scan_line` does not).
- [ ] 46. `redact()` does NOT use `transient_settings` — ADR-005 §Consequences "no `transient_settings`" — `test_no_transient_settings_in_source` (AST/source scan).
- [ ] 47. No YAML config file is read by the redaction module — ADR-005 §Consequences "no YAML config" — `test_no_yaml_config_loaded` (AST/source scan: no `yaml.load`, no `.yml`/`.yaml` file access).
- [ ] 48. Plugin instances are reused across `redact()` calls (instantiated once at module-init time inside the lazy-import block, or cached on first call) rather than recreated per-line — ADR-005 §Decision §1 "Plugin instances live for the life of the redaction function" — `test_plugin_instances_are_cached` (monkeypatch one plugin class to count `__init__` calls; call `redact()` 3 times on multi-line input; assert `__init__` called once, not 3 × N-lines).
- [ ] 49. The `filename` argument passed to `analyze_line` is the literal string `"<daydream>"` — ADR-005 §Consequences "Shape" — `test_analyze_line_filename_is_daydream` (monkeypatch a plugin's `analyze_line` to record args; call `redact("x")`; assert filename).

## J. Events emission (ADR-005 §Consequences "events stream" + ADR-009 shim contract)

ADR-009 (dated 2026-06-21) ships an `emit()` shim at `eval/memeval/dreaming/events.py`. As of this rubric's writing, the events module does not yet exist on disk. If it does NOT exist at PR1 merge, criteria 50–52 are N-A and the work is deferred per the dispatcher's "deferred to PR3/PR4" carve-out — recorded explicitly in the verdict.

- [ ] 50. `redact()` calls `emit("redaction.chunk", plugin=<secret_type>, count=<n>)` once per detector that fired, per `redact()` call — ADR-005 §Consequences "events stream" + ADR-009 — `test_redact_emits_per_plugin_event` (monkeypatch `emit`; assert call shape).
- [ ] 51. When no secrets are detected, `redact()` emits zero events (no `count=0` spam) — slop-detection — `test_redact_emits_nothing_when_clean`.
- [ ] 52. If `emit()` itself raises, `redact()` swallows and continues (fail-open extends to event emission) — ADR-005 §Consequences (fail-open) — `test_emit_failure_does_not_break_redact`.

## K. Anti-slop (deterministic source-scan)

- [ ] 53. Zero `TODO`/`FIXME`/`XXX`/`HACK` comments in `eval/memeval/dreaming/redaction/**/*.py` — slop-detection — `test_no_todo_markers` (grep-equivalent regex scan).
- [ ] 54. Zero plugin classes with a `pass`-only body (each custom plugin sets `secret_type` AND `denylist`; no stub classes) — slop-detection — `test_no_stub_plugin_classes` (AST scan: every class inheriting `RegexBasedDetector` has both attrs assigned at class level).
- [ ] 55. Zero `# pragma: no cover` / `# type: ignore` / `# noqa` in redaction source unless accompanied by an in-line `# REASON: <text>` justifying it — slop-detection — `test_pragmas_are_justified` (regex scan).
- [ ] 56. Zero `print()` statements in redaction source (logging only) — slop-detection — `test_no_print_calls_in_source` (AST scan).
- [ ] 57. Every public function/class in `redaction/` has a one-line docstring naming what it does (not "TODO" / not empty) — slop-detection — `test_public_symbols_have_real_docstrings` (AST scan; failure mode the rule catches: stub docstrings).

## L. ADR-005 §Consequences "Policy" lines — explicit one-to-one mapping

This section guarantees every Policy line in ADR-005 §Consequences has a rubric criterion. If a Policy is not covered here, this rubric is incomplete and the work cannot pass.

- [ ] 58. Policy "every string Daydream passes to `LLMClient.complete()` routes through `redact()` first, including tests" — OUT OF PR1 SCOPE (LLMClient is PR2). Recorded as N-A here; lives in PR2 rubric. — verified: `test_pr1_does_not_introduce_llmclient` (PR1's diff contains no `LLMClient` references; prevents scope creep).
- [ ] 59. Policy "no entropy detectors in v1" — covered by criteria 26, 27, 28. — meta-check: `test_entropy_policy_coverage_holds` (assertion that those tests exist and run).
- [ ] 60. Policy "no `transient_settings`, no `scan_line()`, no YAML config" — covered by criteria 45, 46, 47. — meta-check: `test_driving_mechanism_policy_coverage_holds`.
- [ ] 61. Policy "no network verification" — covered by criterion 38. — meta-check: `test_network_policy_coverage_holds`.
- [ ] 62. Policy "custom plugins under `eval/memeval/dreaming/redaction/plugins/`, each inheriting from `RegexBasedDetector`" — covered by criteria 16, 19, 21, 23, 75, 78 (existence + inheritance, all six plugins) plus `test_custom_plugins_live_in_plugins_subdirectory` (verifies filesystem path).
- [ ] 63. Policy "events stream emission" — covered by criteria 50–52 OR explicitly deferred (see §J preamble). — meta-check: `test_events_policy_coverage_holds_or_explicitly_deferred`.
- [ ] 64. Policy "dependency pin: `detect-secrets==1.5.0` in `eval/pyproject.toml`" — verified by criterion: `test_detect_secrets_pin_is_1_5_0` (parses `eval/pyproject.toml`, asserts exact `==1.5.0` pin in `daydream` extra; spike-validated version per ADR-005).
- [ ] 65. Policy "migration path to harness-005 adapter" — DOCUMENTATION-ONLY policy, no PR1 code surface. N-A.
- [ ] 66. Policy "Daydream chunk-extraction loop is the exhaustive consumer" — PR2+ scope. N-A in PR1.

## M. Test-suite hygiene (the rubric IS the test plan)

- [ ] 67. Every test named in this rubric exists as a function in `eval/memeval/dreaming/tests/test_redaction.py` (or split files: `test_redaction_plugins.py`, `test_redaction_failopen.py`, `test_redaction_anti_slop.py`, `test_redaction_newtype.py`, `test_redaction_audit.py`) — slop-detection — `test_rubric_test_names_all_present` (introspects collected pytest items; asserts every test name in this file is collected).
- [ ] 68. `pytest eval/memeval/dreaming/tests/test_redaction*.py` exits 0 on a clean checkout with `pip install -e eval[daydream]` — verification floor — manual: implementer records the command + exit code in the PR description; reviewer re-runs.
- [ ] 69. The existing `test_detect_secrets_assumptions.py` still passes (no regressions in pinned upstream behavior) — verification floor — manual + CI.
- [ ] 70. PR1's diff touches ONLY paths under `eval/memeval/dreaming/redaction/`, `eval/memeval/dreaming/tests/`, `eval/memeval/dreaming/__init__.py` (the re-export), the repo `.gitignore` (ADR-011 audit pattern, see §O), and any pyproject/Makefile lines needed for the strict-mypy target (see §N). No edits to harness/loader/store code, no edits to existing `LLMClient` paths — scope discipline — manual: `git diff --name-only main...HEAD | sort -u` audited by reviewer.

---

## N. `RedactedText` NewType (ADR-010)

The `RedactedText` NewType is the structural enforcement of the redaction trust boundary. Its presence on `redact()`'s return is non-negotiable; its enforcement is only as real as the typechecker that polices it.

- [ ] 71. `RedactedText` is defined at the top of `eval/memeval/dreaming/redaction/__init__.py` as exactly `RedactedText = NewType("RedactedText", str)` — ADR-010 §Decision — `test_redactedtext_is_newtype_of_str` (imports the symbol; asserts `RedactedText.__supertype__ is str`; asserts `RedactedText.__name__ == "RedactedText"`).
- [ ] 72. `RedactedText` is re-exported from the redaction package's public surface so consumers do `from memeval.dreaming.redaction import RedactedText` — ADR-010 §Consequences "Contract — source of truth" — `test_redactedtext_public_import`.
- [ ] 73. `redact()`'s function body ends with `return RedactedText(cleaned)`, not bare `return cleaned` — ADR-010 §Decision ("`redact()`'s body ends with `return RedactedText(cleaned)`") — `test_redact_body_wraps_return_in_redactedtext` (AST-scan: locate the `redact` function definition; assert the final return statement is a `Call` whose `func` is `Name("RedactedText")`).
- [ ] 74. A negative-typecheck regression test fails mypy when a raw `str` is passed to a function annotated `RedactedText` — ADR-010 §Consequences "Policy — CI runs `mypy --strict`" — `tests/typecheck/test_redactedtext_negative.py` contains a snippet `def f(x: RedactedText): ...` followed by `f("raw string")`; `pytest` invokes `mypy --strict` on that file and asserts mypy exits non-zero AND the error message names `RedactedText`. (Driven via `subprocess.run([sys.executable, "-m", "mypy", "--strict", PATH])`; mypy must be on PATH — covered by §76.)
- [ ] 75. A positive-typecheck regression test passes mypy when `redact()`'s return value is passed to the same function — ADR-010 §Decision — `tests/typecheck/test_redactedtext_positive.py` contains `f(redact("x"))`; mypy exits 0. (Same harness as §74; this protects against §74 vacuously passing because mypy is misconfigured.)
- [ ] 76. mypy is invoked with `--strict` against `eval/memeval/dreaming/` from a reproducible target. Either: (a) a `make typecheck` (or equivalent script) target exists at the repo root that runs `mypy --strict eval/memeval/dreaming/` and exits with mypy's exit code, OR (b) a `.github/workflows/*.yml` step does the same. The presence of either is verified by `test_mypy_strict_target_exists` (parses Makefile lines OR yaml workflow files; asserts the command string includes `mypy --strict` and the target path). ADR-010 §Open items lists CI coordination as deferred — this criterion makes the local reproducibility floor non-deferrable.
- [ ] 77. mypy is declared as a dev/test dependency in `eval/pyproject.toml` (e.g. in the `daydream` or a `dev` extra) — ADR-010 enforcement floor — `test_mypy_declared_as_dependency` (parses `eval/pyproject.toml`; asserts `mypy` appears in an extras_require / optional-dependencies group).
- [ ] 78. The escape hatch `RedactedText(<literal-or-non-redact-string>)` does NOT appear in production source under `eval/memeval/dreaming/` outside `redact()`'s own body and the `tests/` tree — ADR-010 §Rationale (escape hatch is intentionally visible and rare) + §Open items "future audit" — `test_no_unjustified_redactedtext_casts` (grep-based scan: `grep -rn 'RedactedText(' eval/memeval/dreaming/` minus the allowed call sites — the single line in `redact()` body and any line under `tests/`. Any other match must carry an in-line `# REASON: deliberate bypass — <text>` justification; absence of the justification fails the criterion).
- [ ] 79. LLMClient signature update (ADR-010 §Decision: `complete(prompt: RedactedText, *, system: RedactedText | None ...)`) is OUT OF PR1 SCOPE — explicitly tracked here so it doesn't get silently skipped. Covered in PR2 rubric — N-A in PR1. Verified by `test_pr1_does_not_modify_llmclient_signature` (greps PR1's diff: zero hits for `LLMClient` and zero hits for `def complete(`).

## O. Expanded plugin set (ADR-011 §Decision §1) — DatabaseURLDetector + URLCredentialDetector

ADR-011 brings the custom-plugin count from 4 to 6. Both new plugins live in `eval/memeval/dreaming/redaction/plugins/` under the same conventions as the ADR-005 four.

- [ ] 80. `DatabaseURLDetector` exists, inherits `RegexBasedDetector`, sets `secret_type = "Database Connection String"` — ADR-011 §Consequences "Shape" — `test_database_url_plugin_class_shape`.
- [ ] 81. `DatabaseURLDetector.denylist` contains the EXACT regex `re.compile(r"(postgres|postgresql|mysql|mongodb|redis|amqp)://[^:\s]+:[^@\s]+@")` — ADR-011 §Decision §1 — `test_database_url_plugin_regex_verbatim` (introspects `.denylist[0].pattern`; string-compares to ADR-011's regex character-for-character).
- [ ] 82. `DatabaseURLDetector` catches `postgres://user:pw@host/db` — ADR-011 §Decision §1 — `test_database_url_plugin_matches_postgres`.
- [ ] 83. `DatabaseURLDetector` catches all five schemes from the regex (`mysql://`, `mongodb://`, `redis://`, `amqp://`, `postgresql://`) — ADR-011 §Decision §1 — `test_database_url_plugin_matches_all_schemes` (parametrized).
- [ ] 84. `DatabaseURLDetector` does NOT fire on prose ("the postgres database is fast", "see redis://example for docs" without userinfo+`@`) — ADR-011 §Rationale (low FP) — `test_database_url_plugin_ignores_prose`.
- [ ] 85. `URLCredentialDetector` exists, inherits `RegexBasedDetector`, sets `secret_type = "URL-Embedded Credential"` — ADR-011 §Consequences "Shape" — `test_url_credential_plugin_class_shape`.
- [ ] 86. `URLCredentialDetector.denylist` contains the EXACT regex `re.compile(r"[?&](access_token|api_key|auth|token|secret|password)=[^&\s]{6,}")` — ADR-011 §Decision §1 — `test_url_credential_plugin_regex_verbatim` (introspects `.denylist[0].pattern`; string-compares to ADR-011's regex character-for-character).
- [ ] 87. `URLCredentialDetector` catches each of the six credential keys (`access_token`, `api_key`, `auth`, `token`, `secret`, `password`) in a URL query string with a ≥6-char value — ADR-011 §Decision §1 — `test_url_credential_plugin_matches_all_keys` (parametrized over the six keys).
- [ ] 88. `URLCredentialDetector` boundary check: a 6-char value matches; a 5-char value does NOT match (regex requires `{6,}`) — ADR-011 §Decision §1 — `test_url_credential_plugin_length_boundary`.
- [ ] 89. `URLCredentialDetector` does NOT fire on prose ("use the api_key argument", "?token= is empty") — ADR-011 §Rationale (low FP) — `test_url_credential_plugin_ignores_prose`.
- [ ] 90. Both `DatabaseURLDetector` AND `URLCredentialDetector` are in the curated module-level plugin list that `redact()` drives — ADR-011 §Consequences "Contract — source of truth" — `test_new_plugins_in_active_list` (introspects the curated list; asserts both classes are members).
- [ ] 91. The custom-plugin count is EXACTLY 6 (Anthropic, OpenRouter, GoogleCloud, BearerToken, DatabaseURL, URLCredential) — neither more nor fewer — ADR-011 §Consequences "Contract" ("now includes 6 custom plugins") — `test_custom_plugin_count_is_six` (filters the curated list to classes whose module path starts with `memeval.dreaming.redaction.plugins`; asserts `len() == 6`).

## P. Out-of-scope policy (ADR-011 §Decision §2)

ADR-011 §Decision §2 makes the out-of-scope list a contract with downstream users — discoverable in both the module docstring AND the v1 README.

- [ ] 92. `eval/memeval/dreaming/redaction/__init__.py` module docstring contains the literal phrase `Free-form English credentials` and explains they are out of scope — ADR-011 §Consequences "Policy — out-of-scope list" — `test_module_docstring_lists_freeform_english_oos` (introspects `redaction.__doc__`).
- [ ] 93. Module docstring contains the literal phrase `Novel/custom token formats` (or `Novel token formats`) listed as out of scope — ADR-011 §Decision §2 — `test_module_docstring_lists_novel_formats_oos`.
- [ ] 94. Module docstring contains the literal phrase `PII` (in any form: `PII`, `personal names, emails`) listed as out of scope — ADR-011 §Decision §2 — `test_module_docstring_lists_pii_oos`.
- [ ] 95. The v1 README (path: `eval/memeval/dreaming/README.md`, or `eval/memeval/dreaming/redaction/README.md` if implementer prefers component-scoped — pick ONE and document the choice in the PR description) contains the same three out-of-scope categories — ADR-011 §Consequences "Policy — out-of-scope list" ("AND in the v1 README, so it's discoverable both ways") — `test_readme_lists_out_of_scope_categories` (reads the README file; asserts the three category names appear). If the README does not yet exist, create it in PR1 — this criterion is non-deferrable because ADR-011's "discoverable both ways" requirement is explicit.

## Q. FP/FN audit-file writer (ADR-011 §Decision §3)

The audit file is local-only, gitignored, and contains pre-redaction (potentially secret-bearing) text. The PR1 writer accepts a destination path from the caller; resolution per ADR-harness-004 is a PR2/PR3 wiring concern (see judgment-call note in the preamble).

- [ ] 96. A module-private writer function (e.g. `_write_audit_record(path, ts, chunk_id, pre, post, detected)`) exists in the redaction package — ADR-011 §Decision §3 — `test_audit_writer_exists` (imports the writer; asserts callable).
- [ ] 97. The writer appends ONE line per call to the given path — ADR-011 §Decision §3 ("Each line: …") — `test_audit_writer_appends_one_line_per_call` (calls writer 3 times; reads file; asserts 3 lines).
- [ ] 98. Each line is valid JSON with the keys `ts`, `chunk_id`, `pre`, `post`, `detected` — ADR-011 §Decision §3 — `test_audit_writer_record_shape` (parses each line; asserts the five keys exist; asserts no extra keys outside an opt-in allowlist).
- [ ] 99. `ts` is a unix timestamp (int or float seconds since epoch) — ADR-011 §Decision §3 shape — `test_audit_writer_ts_is_unix`.
- [ ] 100. `chunk_id` is an int — ADR-011 §Decision §3 shape — `test_audit_writer_chunk_id_is_int`.
- [ ] 101. `pre` is the raw input string verbatim (no normalization, no truncation) — ADR-011 §Decision §3 + slop-detection — `test_audit_writer_pre_is_verbatim`.
- [ ] 102. `post` is the redacted output string verbatim — ADR-011 §Decision §3 + slop-detection — `test_audit_writer_post_is_verbatim`.
- [ ] 103. `detected` is a dict whose keys are `secret_type` strings and whose values are non-negative ints (counts of redactions per type for this chunk) — ADR-011 §Decision §3 (`"detected": {"AWSKey": 1, "AnthropicKey": 0, ...}`) — `test_audit_writer_detected_is_count_dict` (parametrized over a clean chunk and a chunk with 2 distinct secret types).
- [ ] 104. The writer's target path matches the pattern `<basedir>/dream/<session_id>.redact-audit.jsonl` when the caller supplies `basedir` and `session_id` — ADR-011 §Consequences "Policy — audit file path" — `test_audit_writer_path_pattern` (call helper that composes the path from `(basedir, session_id)`; asserts result ends with `/dream/<session_id>.redact-audit.jsonl`). NOTE: full `${MEMORY_STORE%/*}` env-var resolution is PR2/PR3 wiring per the preamble judgment call; PR1 ships path composition only.
- [ ] 105. The writer creates the parent `dream/` directory if it does not exist — ADR-011 §Decision §3 (writer must work first time on a fresh session) — `test_audit_writer_creates_parent_dir` (point at a tmp path whose parent doesn't exist; assert success).
- [ ] 106. The writer opens the file in append mode (`"a"`), not write mode (`"w"`) — ADR-011 §Decision §3 (one line per chunk, never overwrite) — `test_audit_writer_uses_append_mode` (AST-scan of source: literal `"a"` mode argument to `open()`).

## R. Audit-file local-only invariant (ADR-011 §Consequences "Policy — local-only invariant")

- [ ] 107. The writer makes zero network connections during a write — ADR-011 §Consequences "Policy — local-only invariant" ("never read by the LLM, never transmitted") — `test_audit_writer_makes_no_network_connect` (monkeypatches `socket.socket.connect` to raise; calls writer; asserts no raise from socket).
- [ ] 108. The writer makes zero filesystem writes outside the supplied path — slop-detection on the local-only invariant — `test_audit_writer_writes_only_to_supplied_path` (monkeypatches `builtins.open` to record write-mode opens; calls writer; asserts only the supplied path is opened for write/append).
- [ ] 109. The redaction module never reads the audit file back during a `redact()` call (the file is write-only from the redaction side; read is the eval driver's lane) — ADR-011 §Consequences "Exhaustive consumers" — `test_redact_does_not_read_audit_file` (monkeypatches `builtins.open` to record opens; calls `redact("x")`; asserts no read of any `*.redact-audit.jsonl` path).

## S. Gitignore (ADR-011 §Consequences "Policy — gitignore")

- [ ] 110. The repo root `.gitignore` file exists at `/Users/nerd/Git/agent-memory-harness/.gitignore` (path is repo-relative `.gitignore`) — ADR-011 §Consequences "Policy — gitignore" — `test_repo_gitignore_exists`. NOTE: the file does not exist on disk as of this rubric's writing; PR1 creates it.
- [ ] 111. `.gitignore` contains the literal line `*.redact-audit.jsonl` — ADR-011 §Consequences "Policy — gitignore" ("`*.redact-audit.jsonl` pattern added") — `test_gitignore_contains_redact_audit_pattern` (reads `.gitignore`; asserts the pattern appears as an exact line — not as a substring of a comment).
- [ ] 112. The pattern actually causes git to ignore a created `<x>.redact-audit.jsonl` file at the repo root AND at a nested path — ADR-011 §Consequences — `test_gitignore_pattern_actually_ignores` (uses `git check-ignore` via subprocess on two synthetic paths; asserts both are ignored).

---

## T. ADR-010 + ADR-011 Policy-line one-to-one mapping (mirror of §L)

Same shape as §L for ADR-005. Every Policy line in ADR-010 §Consequences and ADR-011 §Consequences gets a rubric criterion or an explicit deferral.

- [ ] 113. ADR-010 Policy "every prompt-construction site in Daydream and Dreaming produces a `RedactedText` via `redact()`" — PR2+ scope (no prompt-construction sites in PR1). N-A in PR1; lives in PR2 rubric. — meta-check: criterion 79 prevents accidental PR1 entry.
- [ ] 114. ADR-010 Policy "`system` parameter of `complete()` is also `RedactedText`" — PR2 scope. N-A in PR1. — meta-check: criterion 79 prevents accidental PR1 entry.
- [ ] 115. ADR-010 Policy "CI runs `mypy --strict` on `eval/memeval/dreaming/`" — covered by criterion 76. — meta-check: `test_mypy_strict_policy_coverage_holds`.
- [ ] 116. ADR-010 Policy "negative-typecheck test ships with the redaction module" — covered by criterion 74. — meta-check: `test_negative_typecheck_policy_coverage_holds`.
- [ ] 117. ADR-011 Policy "curated list includes 6 custom plugins" — covered by criterion 91. — meta-check: `test_six_plugin_policy_coverage_holds`.
- [ ] 118. ADR-011 Policy "out-of-scope documented in module docstring AND README" — covered by criteria 92–95. — meta-check: `test_out_of_scope_policy_coverage_holds`.
- [ ] 119. ADR-011 Policy "audit-file path resolution per ADR-harness-004" — PR1 ships path composition only (criterion 104). Full env-var resolution deferred to PR2/PR3 per preamble judgment call. — meta-check: explicit deferral recorded; criterion 104 is the PR1 coverage line.
- [ ] 120. ADR-011 Policy "gitignore `*.redact-audit.jsonl`" — covered by criteria 110–112. — meta-check: `test_gitignore_policy_coverage_holds`.
- [ ] 121. ADR-011 Policy "local-only invariant tested by regression" — covered by criteria 107–109. — meta-check: `test_local_only_policy_coverage_holds`.
- [ ] 122. ADR-011 Policy "audit-file retention TTL — paired with ADR-009 events diary" — Open item in ADR-011; no PR1 code surface (no retention sweeper yet). N-A in PR1.
- [ ] 123. ADR-011 Policy "eval driver FP/FN computation" — cross-domain (Ken's lane). Not PR1 scope. N-A in PR1.

---

**Pass condition:** every box checked. Any FAIL or any unchecked-without-N-A-justification = NOT DONE; the work is not ready for merge.
