# Stop-hook → daydream migration PR — done rubric

Implementer checks each item off before marking PR ready for review. Jasnah re-verifies on review. Anything fuzzy or unchecked = NOT DONE. Each criterion is boolean (PASS / FAIL / N-A). No partial credit. No compounds — every "and" is split into its own line.

Scope: the three-change migration in OUR (dreaming) lane that closes the gap between PR5 (`daydream-cli daydream` shipped as a console script) and the Claude Code plugin (Stop-hook handler is a no-op observer today). After this PR, a real CC session's `Stop` / `PreCompact` hook fires Daydream extraction. This rubric covers ONLY the dreaming-domain slices of Halliday's bench-readiness audit — explicit cross-domain concerns (Brent's Router contract, Ken's bench topology) are NOT in scope.

The three changes:

1. `plugin/cookbook_memory/adapters/claude_code/hooks_handler.py` — `handle()` shells out to `daydream-cli daydream` on `Stop` / `PreCompact`; retains the prior no-op-with-note-event behavior on every other event.
2. `eval/memeval/dreaming/cli.py` — `daydream-cli daydream` emits an `OPENROUTER_API_KEY`-unset startup alert (stderr + WARNING log + `daydream.openrouter_unset` diary event) before engine work; engine still runs and fail-opens.
3. `eval/memeval/claudecode/plugin/README.md` — `**DEPRECATED**` banner pointing at the new `plugin/cookbook_memory/` tree. Tree NOT deleted (per user — wait for green bench).

NOT in scope (carved out, do NOT fail PR for missing these — see §P):

- **`Router.write` swap** at `_make_store` / `_Engine.remember`. Brent's contract decision; tagged in `/tmp/team-coordination-bench-readiness.md` as a cross-domain ask. NOT touched here.
- **`_solve_plugin_real` topology** + **`run_bench.py` env gate** — Ken's lane.
- The `daydream.precompact_skipped_stop_running` event (ADR-017 open item; engine-only).
- Transcript-path hardening (ADR-017 carve-out).
- Night-dream worker body (`DreamingWorker.run` still raises `NotImplementedError`).
- Deletion of the legacy `eval/memeval/claudecode/plugin/` tree (deferred until green bench).
- ADR-001 amendment recording the subprocess-shell-out shape (text-only successor PR).

Anchors:

- ADR-001 = `docs/adrs/ADR-dreaming-001-daydreaming-stop-fired.md` (Stop-hook contract).
- ADR-017 = `docs/adrs/ADR-dreaming-017-precompact-concurrency-and-transcript-trust.md`.
- ADR-018 = `docs/adrs/ADR-dreaming-018-cli-argparse-exit-code.md` (exit-1-not-2).
- ADR-019 = `docs/adrs/ADR-dreaming-019-memory-store-is-a-directory.md`.
- ADR-harness-006 = `docs/adrs/ADR-harness-006-fail-open.md`.
- PR5 rubric = `eval/memeval/dreaming/tests/PR5_DAYDREAM_CLI_RUBRIC.md` (format template).
- Migration plan = `/tmp/MIGRATION_PR_PLAN.md`.
- Halliday audit = `/tmp/team-coordination-bench-readiness.md`.

Per-PR judgment calls (recorded once, not re-litigated per criterion):

- **Subprocess vs in-process import.** The handler `subprocess.run`s `daydream-cli daydream` (NOT `from memeval.dreaming.engine import daydream`). Rationale: preserves the import-isolation seam Keith built; keeps `detect-secrets` + `httpx` out of the hook process; PATH-dependency is acceptable (resolved by `pip install eval[daydream]`). Criteria 1–14 pin the shape.
- **Subprocess literal command** is `["daydream-cli", "daydream"]` — list form, NOT `shell=True`, NOT string interpolation.
- **Stdin passthrough.** The handler forwards the verbatim payload as `json.dumps(payload)` into the subprocess stdin. NO rewriting, NO filtering, NO adding fields.
- **Selective env passthrough** (halliday F4). The handler passes ONLY a whitelisted minimum env to the subprocess: `PATH` (resolve daydream-cli), `HOME`, `LANG` (+ locale vars), plus `MEMORY_STORE` (from `settings.store_path` when set), `OPENROUTER_API_KEY`, and any `DREAM_*` vars (`DREAM_PROVIDER`, `DREAM_MODEL`, `DREAM_RETENTION_DAYS`, `DREAM_SWEEP_INTERVAL_MIN`). Everything else in `os.environ` is dropped — minimum-surface, no accidental secret-leak.
- **Per-event timeout** (halliday F11). `Stop` (async, CC has detached) subprocess timeout is `600`s. `PreCompact` (sync, CC blocks) subprocess timeout is `120`s — shorter to avoid blocking the user on a long-running daydream while compaction waits.
- **Async semantics.** Both `plugin/cookbook_memory/adapters/claude_code/hooks/hooks.json` AND `plugin/marketplace/cookbook-memory/hooks/hooks.json` keep `async: true` on Stop (verified in both at this PR baseline). The handler's `subprocess.run` is synchronous from the handler's perspective, but CC has detached the handler process for Stop. (Both files are owned by Keith / the harness lane — this rubric does NOT pin or freeze them.)
- **Fail-open contract for the subprocess wrapper.** Every exception class — `subprocess.TimeoutExpired`, `FileNotFoundError` (daydream-cli not on PATH), `subprocess.CalledProcessError`, any other `Exception` — gets caught, emits a `daydream.hook_subprocess_failed` event via the existing `EventStream`, and `handle()` returns `{}`. `KeyboardInterrupt` / `SystemExit` propagate. For `FileNotFoundError` specifically, the handler ALSO writes a one-line stderr message naming `daydream-cli` (halliday F5) so a sync-PreCompact user sees the missing-PATH signal in their terminal.
- **Event gating.** The handler retains the prior `note` event on every event, AND emits a NEW `daydream.hook_subprocess_fired` event ONLY when it shells out (event_name in `{"Stop", "PreCompact"}`). On a fail-open exception path, the additional `daydream.hook_subprocess_failed` event is emitted.
- **Non-Stop/PreCompact regression guard.** The handler's prior behavior on `SessionStart` / `UserPromptSubmit` / `PostCompact` is byte-equivalent (records a `note` event, returns `{}`, does NOT spawn a subprocess).
- **OPENROUTER alert shape.** When `OPENROUTER_API_KEY` is unset at entry to `_handle_daydream` (BEFORE the `try:` block that wraps engine work), the CLI emits: (1) one stderr line containing literal `OPENROUTER_API_KEY` AND `.env.example`; (2) one WARNING-or-higher log record naming the env var; (3) one `daydream.openrouter_unset` diary event (the only observable signal in CC's async-Stop path — halliday F9). Engine work proceeds; fail-open semantics unchanged.
- **Deprecation banner.** `eval/memeval/claudecode/plugin/README.md`'s first 30 lines contain the literal substring `**DEPRECATED**` AND the literal substring `plugin/cookbook_memory/`. The README file is NOT deleted in this PR.
- **Integration test placement.** New test lives at `plugin/tests/test_hooks_handler_subprocess.py`. The dreaming-side OPENROUTER-alert tests live in `eval/memeval/dreaming/tests/test_cli.py` (extension of the existing file).
- **Diff scope discipline.** PR diff touches ONLY: `plugin/cookbook_memory/adapters/claude_code/hooks_handler.py`, `eval/memeval/dreaming/cli.py`, `eval/memeval/claudecode/plugin/README.md`, plus the new test file, plus the test extensions to `eval/memeval/dreaming/tests/test_cli.py`, plus this rubric. NO touches to `engine.py`, `_state.py`, `_extract.py`, `redaction/`, `schema.py`, `protocols.py`, `router.py`, OR `plugin/cookbook_memory/core/client.py` (Router.write swap is Brent's lane, NOT here).

---

## A. `hooks_handler.handle()` — subprocess wiring on Stop / PreCompact

- [ ] 1. `plugin/cookbook_memory/adapters/claude_code/hooks_handler.py` imports `subprocess` at module top — slop-detection (lazy import here adds nothing; subprocess is stdlib) — `test_hooks_handler_imports_subprocess` (AST scan of module-level imports).
- [ ] 2. `hooks_handler.py` imports `os` at module top — slop-detection — `test_hooks_handler_imports_os` (AST scan).
- [ ] 3. The `handle()` function's body contains an `if event_name in {"Stop", "PreCompact"}:` branch (or equivalent membership test against the same two literal strings, in either order) gating the subprocess call — change #1 — `test_handle_gates_on_stop_or_precompact` (AST scan: locate the `If` node whose test is a `Compare` of `event_name` against a `Set`/`Tuple`/`List` containing exactly the string constants `"Stop"` and `"PreCompact"`).
- [ ] 4. When `handle()` is invoked with `event_name="Stop"` and a monkeypatched `subprocess.run`, `subprocess.run` is called EXACTLY ONCE — change #1 — `test_handle_calls_subprocess_run_once_on_stop` (in-process; monkeypatch `subprocess.run` with a recorder).
- [ ] 5. The first positional arg to `subprocess.run` is the literal list `["daydream-cli", "daydream"]` (list form; len == 2; element 0 == `"daydream-cli"`; element 1 == `"daydream"`) — judgment call "Subprocess literal command" — `test_subprocess_call_uses_daydream_cli_daydream_list_form` (assert the recorded call's `args[0] == ["daydream-cli", "daydream"]`).
- [ ] 6. `subprocess.run` is invoked with `shell=False` (the explicit kwarg OR omitted, which defaults to `False`) — judgment call — `test_subprocess_call_does_not_use_shell` (assert recorded `kwargs.get("shell", False) is False`).
- [ ] 7. When `handle()` is invoked with a payload dict, the recorded `subprocess.run` call's `input` kwarg equals `json.dumps(payload)` exactly (string-equal; whitespace-equal; key-order-equal — i.e., the implementer did NOT re-serialize through a different dumper that could permute keys) — judgment call "Stdin passthrough" — `test_subprocess_input_is_verbatim_json_dumps_payload` (assert `recorded_kwargs["input"] == json.dumps(payload)`).
- [ ] 8. The recorded `subprocess.run` call's `env` kwarg is a `dict` that contains every key from `os.environ` (superset check) — judgment call "Env passthrough" — `test_subprocess_env_contains_os_environ` (assert `os.environ.items() <= recorded_kwargs["env"].items()`).
- [ ] 9. The recorded `subprocess.run` call's `env` kwarg's `"MEMORY_STORE"` key equals `str(settings.store_path)` when `settings.store_path` is not `None` — judgment call "Env passthrough" — `test_subprocess_env_injects_memory_store_when_settings_has_path` (construct a Settings with a real path; assert the env carries it).
- [ ] 10. When `settings.store_path` is `None`, the recorded `subprocess.run` call's `env` kwarg does NOT contain a `MEMORY_STORE` key beyond whatever was inherited from `os.environ` — judgment call "Env passthrough" — `test_subprocess_env_omits_memory_store_when_settings_has_none` (clear `os.environ["MEMORY_STORE"]`; construct Settings with `store_path=None`; assert `"MEMORY_STORE" not in recorded_kwargs["env"]`).
- [ ] 11. The recorded `subprocess.run` call's `timeout` kwarg is a positive integer — judgment call — `test_subprocess_call_has_positive_timeout` (assert `isinstance(recorded_kwargs["timeout"], int) and recorded_kwargs["timeout"] > 0`).
- [ ] 12. When `event_name="Stop"`, the recorded `subprocess.run` call's `timeout` kwarg is exactly `600` (matches the hooks.json convention) — `test_subprocess_timeout_is_600s_on_stop`.
- [ ] 12b. When `event_name="PreCompact"`, the recorded `subprocess.run` call's `timeout` kwarg is exactly `120` (shorter; PreCompact is sync and CC blocks — halliday F11) — `test_subprocess_timeout_is_120s_on_precompact`.
- [ ] 13. `hooks_handler.py` source contains zero matches for the literal string `shell=True` — slop-detection — `test_no_shell_true_in_hooks_handler` (read source; assert `"shell=True" not in source`).
- [ ] 14. `hooks_handler.py` source contains zero matches for `os.system` AND zero matches for `subprocess.Popen` with `shell=True` — slop-detection — `test_no_os_system_in_hooks_handler` (AST scan: no `Call` whose func attribute path is `os.system`; no `Call` to `subprocess.Popen` with `shell=True`).

## B. Subprocess fail-open contract

- [ ] 15. When `subprocess.run` raises `subprocess.TimeoutExpired`, `handle()` returns `{}` — judgment call "Fail-open contract for the subprocess wrapper" + ADR-harness-006 — `test_handle_failopens_on_timeoutexpired` (monkeypatch `subprocess.run` to raise; assert `handle("Stop", {}) == {}`).
- [ ] 16. When `subprocess.run` raises `FileNotFoundError` (daydream-cli not on PATH), `handle()` returns `{}` — ADR-harness-006 — `test_handle_failopens_on_filenotfounderror`.
- [ ] 17. When `subprocess.run` raises `subprocess.CalledProcessError`, `handle()` returns `{}` — ADR-harness-006 — `test_handle_failopens_on_calledprocesserror`.
- [ ] 18. When `subprocess.run` raises any other `Exception` subclass (e.g., `RuntimeError`), `handle()` returns `{}` — ADR-harness-006 — `test_handle_failopens_on_unexpected_exception`.
- [ ] 19. When `subprocess.run` raises `KeyboardInterrupt`, `handle()` re-raises (does NOT swallow) — slop-detection (mirrors PR5 criterion 42) — `test_handle_does_not_swallow_keyboardinterrupt`.

## C. Non-Stop/PreCompact regression guard

- [ ] 20. When `handle()` is invoked with `event_name="SessionStart"` and a monkeypatched `subprocess.run`, `subprocess.run` is called EXACTLY ZERO times — judgment call "Non-Stop/PreCompact regression guard" — `test_handle_does_not_spawn_subprocess_on_sessionstart` (in-process; monkeypatch `subprocess.run`; assert call count == 0).
- [ ] 21. When `handle()` is invoked with `event_name="UserPromptSubmit"`, `subprocess.run` is called ZERO times — same — `test_handle_does_not_spawn_subprocess_on_userpromptsubmit`.
- [ ] 22. When `handle()` is invoked with `event_name="PostCompact"`, `subprocess.run` is called ZERO times — same — `test_handle_does_not_spawn_subprocess_on_postcompact`.
- [ ] 23. When `handle()` is invoked with `event_name="Stop"`, a `note` event is still emitted via `EventStream.emit` (regression guard on the pre-existing observation behavior) — change #1 (additive, not replacing) — `test_handle_still_emits_note_event_on_stop` (monkeypatch `EventStream.emit`; assert at least one call whose first positional arg is `"note"`).

## D. Subprocess-fire / failure events

- [ ] 24. When `handle()` successfully invokes `subprocess.run` on `event_name="Stop"`, a `daydream.hook_subprocess_fired` event is emitted via `EventStream.emit` exactly once (in addition to the pre-existing `note` event) — change #1 (observability) — `test_handle_emits_hook_subprocess_fired_event_on_stop` (monkeypatch `subprocess.run` to return a `CompletedProcess`; monkeypatch `EventStream.emit`; filter calls by event name; assert count == 1).
- [ ] 25. When `subprocess.run` raises any caught exception, a `daydream.hook_subprocess_failed` event is emitted via `EventStream.emit` exactly once — judgment call — `test_handle_emits_hook_subprocess_failed_event_on_exception` (parametrize over TimeoutExpired/FileNotFoundError/CalledProcessError/RuntimeError; for each, assert one call with that event name).
- [ ] 26. The `daydream.hook_subprocess_failed` event's kwargs include a field whose value is the literal string name of the exception class (e.g., `"TimeoutExpired"`) — judgment call — `test_failed_event_records_exception_class_name`.
- [ ] 27. The `daydream.hook_subprocess_fired` event is NOT emitted when `event_name="SessionStart"` (regression guard) — `test_fired_event_not_emitted_on_non_gated_events`.

## E. OPENROUTER_API_KEY startup alert in `daydream-cli daydream`

- [ ] 28. `eval/memeval/dreaming/cli.py:_handle_daydream` checks `os.environ.get("OPENROUTER_API_KEY")` (or `"OPENROUTER_API_KEY" in os.environ`) at the entry of the function, BEFORE the `try:` block that catches engine exceptions — judgment call "OPENROUTER alert shape" — `test_handle_daydream_checks_openrouter_key_before_engine` (AST scan: locate the `Compare`/`Call` whose subject mentions `OPENROUTER_API_KEY`; assert it appears before the first `Try` node in `_handle_daydream`).
- [ ] 29. When `OPENROUTER_API_KEY` is unset and `_handle_daydream` is invoked (in-process), exactly one stderr line is written whose text contains the literal substring `OPENROUTER_API_KEY` — judgment call — `test_openrouter_unset_emits_stderr_alert` (capsys; assert `"OPENROUTER_API_KEY" in captured.err`; assert the err is non-empty).
- [ ] 30. The stderr alert text contains the literal substring `.env.example` — judgment call — `test_openrouter_alert_names_env_example` (capsys; assert `".env.example" in captured.err`).
- [ ] 31. When `OPENROUTER_API_KEY` is unset, exactly one WARNING-or-higher log record is emitted whose message contains the literal substring `OPENROUTER_API_KEY` — judgment call — `test_openrouter_unset_emits_warning_log` (caplog at WARNING; filter records by message substring; assert count == 1).
- [ ] 32. When `OPENROUTER_API_KEY` IS set (to any non-empty string), zero stderr lines containing `OPENROUTER_API_KEY` are written by the CLI — judgment call (no false positives) — `test_openrouter_set_emits_no_alert`.
- [ ] 33. When `OPENROUTER_API_KEY` IS set, zero WARNING-or-higher log records mentioning `OPENROUTER_API_KEY` are emitted — same — `test_openrouter_set_emits_no_warning_log`.
- [ ] 34. When `OPENROUTER_API_KEY` is unset, `_handle_daydream` STILL proceeds to call `engine.daydream` exactly once (the alert does NOT short-circuit) — judgment call "Engine work proceeds regardless" — `test_openrouter_unset_does_not_short_circuit_engine` (monkeypatch `engine.daydream` with a recorder; unset the env var; invoke `_handle_daydream` with valid args; assert `engine.daydream` was called once).
- [ ] 35. When `OPENROUTER_API_KEY` is unset, the CLI's `main` still returns `0` (fail-open semantics preserved) — judgment call — `test_openrouter_unset_failopens_zero`.
- [ ] 35b. When `OPENROUTER_API_KEY` is unset, exactly one `daydream.openrouter_unset` diary event is emitted via the events shim — halliday F9 (the diary is the only observable signal in CC's async-Stop path where stderr may be discarded) — `test_openrouter_unset_emits_diary_event` (monkeypatch `events.emit`; filter calls by event name; assert count == 1).

## F. Selective env passthrough + FileNotFoundError stderr (halliday F4 + F5)

- [ ] 36. The recorded `subprocess.run` call's `env` kwarg is a `dict` whose keys are a SUBSET of the union of `{"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "LOGNAME", "USER", "TMPDIR", "MEMORY_STORE", "OPENROUTER_API_KEY", "DREAM_PROVIDER", "DREAM_MODEL", "DREAM_RETENTION_DAYS", "DREAM_SWEEP_INTERVAL_MIN"}` — minimum-surface env. NOT a superset of `os.environ` — `test_subprocess_env_is_minimum_surface` (run `handle("Stop", payload)` with `os.environ` containing a sentinel non-allowlisted var like `ANTHROPIC_API_KEY=test`; assert the recorded env dict does NOT contain that key).
- [ ] 37. When `os.environ["OPENROUTER_API_KEY"]` is set at the time `handle()` runs, the recorded subprocess env's `OPENROUTER_API_KEY` equals the parent value — `test_subprocess_env_passes_openrouter_through`.
- [ ] 38. When `os.environ` contains `ANTHROPIC_API_KEY` or any non-allowlisted secret-shaped var, the recorded subprocess env does NOT contain it (selective filter is on by default) — `test_subprocess_env_drops_unknown_secrets`.
- [ ] 39. When `subprocess.run` raises `FileNotFoundError` (daydream-cli not on PATH), the handler writes a one-line message to `sys.stderr` containing the literal substring `daydream-cli` — halliday F5; preserves observability in sync PreCompact + manual contexts — `test_handle_writes_filenotfounderror_message_to_stderr` (capsys; monkeypatch `subprocess.run` to raise FileNotFoundError; invoke `handle("Stop", payload)`; assert `"daydream-cli" in captured.err`).

## H. Deprecation banner on legacy plugin tree

- [ ] 45. `eval/memeval/claudecode/plugin/README.md` exists at this path (NOT deleted) — judgment call "Tree NOT deleted" — `test_legacy_plugin_readme_exists`.
- [ ] 46. The first 30 lines of `eval/memeval/claudecode/plugin/README.md` contain the literal substring `**DEPRECATED**` (exact match, including the asterisks) — change #4 — `test_legacy_plugin_readme_has_deprecated_banner` (read; split on newline; take first 30; assert `"**DEPRECATED**" in "\n".join(first_30)`).
- [ ] 47. The first 30 lines of `eval/memeval/claudecode/plugin/README.md` contain the literal substring `plugin/cookbook_memory/` — change #4 (banner names the successor path) — `test_legacy_plugin_readme_points_at_successor`.
- [ ] 48. `eval/memeval/claudecode/plugin/` (the legacy tree) is NOT removed in this PR's diff — judgment call — `test_legacy_plugin_tree_not_deleted` (manual; `git diff --name-status main...HEAD | grep -E "^D\s+eval/memeval/claudecode/plugin/"` returns empty).

## I. Integration test for the wiring (Stop-fires-daydream)

- [ ] 49. A new test file exists at `plugin/tests/test_hooks_handler_subprocess.py` — judgment call "Integration test placement" — `test_hooks_handler_subprocess_test_file_exists`.
- [ ] 50. The new test file contains a test that invokes `cookbook_memory.adapters.claude_code.hooks_handler.main(["Stop"])` with a stdin JSON payload pointing at a real transcript fixture AND a monkeypatched `subprocess.run` — change #1 verification — `test_main_stop_invokes_subprocess_run` (pytest collects the test by name).
- [ ] 51. The same test asserts that `subprocess.run` was called with the literal list `["daydream-cli", "daydream"]` as `args[0]` — change #1 — `test_main_stop_subprocess_command_is_daydream_cli_daydream`.
- [ ] 52. The same test asserts that the recorded subprocess input is the verbatim JSON of the stdin payload (`json.dumps(payload)`) — change #1 — `test_main_stop_subprocess_input_is_verbatim_payload`.
- [ ] 53. A separate test asserts that invoking `main(["SessionStart"])` with a non-empty stdin payload does NOT call `subprocess.run` (zero calls) — regression guard — `test_main_sessionstart_does_not_invoke_subprocess`.
- [ ] 54. A real-end-to-end test (no `subprocess.run` mocking; real `daydream-cli` on PATH; an `EchoClient` for the LLM; `MEMORY_STORE` set to a temp dir; `OPENROUTER_API_KEY=unset`) invokes `main(["Stop"])` and `proc.wait()`s for the subprocess to finish before reading the store. Asserts: exit 0; at least one MarkdownStore `.md` file under `<MEMORY_STORE>/markdown/memory/` (this is the EXISTING PR5 write path — MarkdownStore direct — NOT the deferred Router.write swap) — change #1 end-to-end — `test_main_stop_writes_memoryitem_end_to_end`. (Marked `pytest.mark.integration` if the implementer wants to gate it on a flag; not skipped by default.)

## J. Diff scope discipline

- [ ] 55. `git diff --name-only main...HEAD | sort -u` against the migration branch lists ONLY paths from this set: `plugin/cookbook_memory/adapters/claude_code/hooks_handler.py`, `eval/memeval/dreaming/cli.py`, `eval/memeval/claudecode/plugin/README.md`, `plugin/tests/test_hooks_handler_subprocess.py`, `plugin/tests/test_adapter_claude_code.py` (existing test update — pre-PR test asserted no-op-on-Stop, now updated to use a non-gated event), `eval/memeval/dreaming/tests/test_cli.py` (test extensions for OPENROUTER alert), this rubric file `eval/memeval/dreaming/tests/MIGRATION_STOP_HOOK_RUBRIC.md` — judgment call "Diff scope discipline" — manual: reviewer runs the diff command and verifies.
- [ ] 56. PR diff does NOT touch `eval/memeval/dreaming/engine.py` — scope discipline — `test_diff_does_not_touch_engine_py` (manual; `git diff --name-only main...HEAD | grep -x eval/memeval/dreaming/engine.py` returns empty).
- [ ] 57. PR diff does NOT touch `eval/memeval/dreaming/_state.py` — `test_diff_does_not_touch_state_py`.
- [ ] 58. PR diff does NOT touch `eval/memeval/dreaming/_extract.py` — `test_diff_does_not_touch_extract_py`.
- [ ] 59. PR diff does NOT touch any file under `eval/memeval/dreaming/redaction/` — `test_diff_does_not_touch_redaction_dir`.
- [ ] 60. PR diff does NOT touch `eval/memeval/schema.py` — frozen contract — `test_diff_does_not_touch_schema_py`.
- [ ] 61. PR diff does NOT touch `eval/memeval/protocols.py` — frozen contract — `test_diff_does_not_touch_protocols_py`.
- [ ] 62. PR diff does NOT touch `eval/memeval/router.py` — Brent's domain — `test_diff_does_not_touch_router_py`.
- [ ] 62b. PR diff does NOT touch `plugin/cookbook_memory/core/client.py` — `_Engine.remember` Router.write swap is Brent's lane / cross-domain ask, NOT this PR's responsibility — `test_diff_does_not_touch_engine_client_py`.

## K. Anti-slop (deterministic source-scan)

Mirrors PR5 §L; applied to every file modified in this PR.

- [ ] 63. Zero `TODO` / `FIXME` / `XXX` / `HACK` comments in: `hooks_handler.py`, `cli.py` (the two modified source files in this PR's lane) — slop-detection — `test_no_todo_markers_in_migration_modules` (read each; assert none of the markers).
- [ ] 64. Zero `print()` calls anywhere in `hooks_handler.py` — slop-detection (logging only; the OPENROUTER alert lives in `cli.py`, NOT here) — `test_no_print_in_hooks_handler` (AST scan).
- [ ] 65. The OPENROUTER stderr alert in `cli.py` uses `print(..., file=sys.stderr)` exactly once, OR `sys.stderr.write(...)` exactly once (one or the other; both is a FAIL because the alert must fire once) — judgment call "Both fire exactly once" — `test_openrouter_alert_writes_to_stderr_exactly_once` (AST scan of `_handle_daydream`: count `Call`s with either signature; assert sum == 1).
- [ ] 66. Zero bare `except:` clauses in `hooks_handler.py`, `cli.py` (every `except` names a class) — slop-detection — `test_no_bare_except_in_migration_modules` (AST scan per file).
- [ ] 67. Every `# pragma: no cover` / `# type: ignore` / `# noqa` in any modified file is accompanied by an inline `# REASON: <text>` justification — slop-detection — `test_pragmas_are_justified_in_migration_modules`.
- [ ] 68. Every public function/class added or modified in this PR has a one-line docstring naming what it does — slop-detection — `test_public_symbols_have_docstrings_in_migration_modules`.
- [ ] 69. The handler's `handle()` function source contains zero matches for the substring `daydream-cli daydream` as a single space-separated string literal (would indicate `shell=True` interpolation creeping back) — slop-detection — `test_handle_does_not_string_interpolate_command` (read source; assert `'"daydream-cli daydream"' not in source` and `"'daydream-cli daydream'" not in source`).

## L. End-to-end smoke (manual, PR-body gate)

Minimum-viable shell smokes the rubric considers the verification floor. All manual; implementer records exact commands + exit codes + paths-after in the PR description for the reviewer to re-run.

- [ ] 70. **E2E happy path with OPENROUTER_API_KEY set** — manual:
  1. `pip install -e eval[daydream]` + `pip install -e plugin` in a clean venv.
  2. `export OPENROUTER_API_KEY=<real-key>`
  3. `export MEMORY_STORE=$(mktemp -d)`
  4. `printf 'session start\nuser: hello\n' > /tmp/fake-session.log`
  5. `echo '{"session_id":"smoke","transcript_path":"/tmp/fake-session.log","hook_event_name":"Stop"}' | python3 -m cookbook_memory.adapters.claude_code.hooks_handler Stop`
  6. Expected: exit 0; `$MEMORY_STORE/dream/smoke.daydream-events.jsonl` exists and contains a `daydream.cli_resolved` event AND a `daydream.memory_written` (or equivalent extraction event); `$MEMORY_STORE/markdown/memory/*.md` exists and contains at least one MemoryItem.
  Implementer records: exit code, the four file paths' presence + sizes, one event line excerpted from the diary.
- [ ] 71. **E2E OPENROUTER_API_KEY unset** — manual:
  1. Same venv as criterion 70.
  2. `unset OPENROUTER_API_KEY`
  3. `export MEMORY_STORE=$(mktemp -d)`
  4. `echo '{"session_id":"smoke","transcript_path":"/tmp/fake-session.log","hook_event_name":"Stop"}' | python3 -m cookbook_memory.adapters.claude_code.hooks_handler Stop 2>/tmp/stderr.log`
  5. Expected: exit 0; `/tmp/stderr.log` contains the literal substring `OPENROUTER_API_KEY`; `/tmp/stderr.log` contains the literal substring `.env.example`.
  Implementer records: exit code + the relevant stderr excerpt.
- [ ] 72. **E2E SessionStart no-op regression** — manual:
  1. Same venv.
  2. `echo '{"session_id":"smoke","hook_event_name":"SessionStart"}' | python3 -m cookbook_memory.adapters.claude_code.hooks_handler SessionStart`
  3. Expected: exit 0; no `dream/` subdirectory created under `$MEMORY_STORE`; no `markdown/memory/*.md` files created.
  Implementer records: exit code + before/after listing of `$MEMORY_STORE`.
- [ ] 73. **E2E bench preflight** — manual:
  1. `memeval-bench --mode plugin-real memoryagentbench --tasks 1 --dry-run` (or the closest dry-run flag available) exits 0 AND does NOT print a banner like "hook handler is a no-op observer".
  Implementer records: exit code + the relevant output line.

## M. Test-suite hygiene

- [ ] 74. `pytest plugin/tests/test_hooks_handler_subprocess.py eval/memeval/dreaming/tests/test_cli.py -q` exits 0 on a clean checkout with `pip install -e eval[daydream]` + `pip install -e plugin` — verification floor — manual: implementer records command + exit code; reviewer re-runs.
- [ ] 75. `pytest plugin/cookbook_memory/tests/ -q` exits 0 (no regressions in the plugin test suite) — manual + CI.
- [ ] 76. `pytest eval/memeval/dreaming/tests/ -q` exits 0 (no regressions in the dreaming test suite) — manual + CI.
- [ ] 77. PR description contains a section titled `Known limitation` (or `Known limitations`) that names the explicit out-of-scope items (PreCompact-skip-when-Stop-running event, transcript-path hardening, night-dream worker body, legacy-tree deletion) — judgment call — manual: reviewer reads PR body.

## N. mypy `--strict` coverage

- [ ] 78. `mypy --strict eval/memeval/dreaming/` exits 0 on a clean checkout AFTER the `cli.py` changes — PR5 §N inheritance — manual: implementer records.
- [ ] 79. `mypy --strict plugin/cookbook_memory/` exits 0 on a clean checkout AFTER the `hooks_handler.py` + `client.py` changes — manual: implementer records.

## O. Rubric adversarial pass — mandatory per Jasnah's persona

Two findings recorded so the dispatcher reviewing this rubric can see them.

- [ ] 80. **Adversarial finding #1 — what might this rubric miss?**
  - **CC plugin runtime drift.** The rubric asserts that `daydream-cli daydream` is the bare command (criterion 5) and that stdin JSON is the only data conveyance (criterion 7). It does NOT verify that the actual `hooks.json` files (`plugin/cookbook_memory/adapters/claude_code/hooks/hooks.json` AND `plugin/marketplace/cookbook-memory/hooks/hooks.json`) still invoke `python3 -m cookbook_memory.adapters.claude_code.hooks_handler Stop` rather than `daydream-cli daydream` directly. The migration plan keeps the existing handler-as-shim shape (CC invokes the handler, handler invokes `daydream-cli`), but a future implementer who skips the handler step would silently regress the OPENROUTER alert path and the `daydream.hook_subprocess_fired` observability. **RUBRIC_GAP:** the hooks.json shape is not pinned. Recommend halliday consider whether to add a sha256-pin on the two hooks.json files in a follow-up rubric (or whether this is out-of-scope because CC-marketplace publication moves them).
  - **Env-passthrough secret-leak risk.** Criterion 8 asserts the subprocess inherits the full `os.environ`. That includes `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / any other secret the user has in their shell. The migration plan's open question #2 raises this; the rubric punts on it (declares "no env filtering in v1"). **RUBRIC_GAP:** if the daydream-cli subprocess gets compromised or logs its env, secrets leak. Acceptable for v1 personal-machine eval (matches PR5's deferred threat model); flag to halliday so he can confirm.
  - **Concurrent Stop race** (halliday F6). Two fast-rerun Stop events for the same `session_id` spawn two `daydream-cli` subprocesses. Engine-level flock (ADR-014) serializes inside the engine; one wins the lock, the other blocks up to 600s. Since CC has already detached both via `async: true`, no user-visible failure. NOTE-ONLY: known + tolerated; ADR-014's flock is the load-bearing seam.

- [ ] 81. **Adversarial finding #2 — scope honesty (necessary-but-not-sufficient).**
  - This rubric covers ONLY the three dreaming-lane changes. PASS on every criterion here does NOT imply a green `memeval-bench --mode plugin-real`. The bench requires ALSO:
    - Ken's `run_bench.py` env-gate refusing to start without `OPENROUTER_API_KEY` (eval lane);
    - Ken's `_solve_plugin_real` topology rewrite that drives Claude through one turn per `task.sessions[i]` so Stop fires (eval lane);
    - Brent's confirm-or-refute on `Router.write` vs. markdown-direct (storage lane).
  - These are tracked in `/tmp/team-coordination-bench-readiness.md` as cross-domain asks. This PR's PR-body MUST link that file and name the asks so a reviewer doesn't expect a green bench from this PR alone.
  - **RUBRIC_GAP requiring structural rewrite:** none. The drift is by design and explicitly documented.

---

## P. Explicitly NOT gated by this PR (carved out — do not fail PR for missing these)

Reviewers MUST NOT FAIL this PR for the absence of any of the following:

- **`Router.write` swap at `_make_store` / `_Engine.remember`.** Brent's contract decision (storage lane). Tracked as a cross-domain ask in `/tmp/team-coordination-bench-readiness.md`. Will be a follow-up PR after Brent confirms.
- **`run_bench.py` env-gate** + **`_solve_plugin_real` topology rewrite.** Ken's eval lane. Same coordination doc.
- **`hooks.json` shape pinning** (canonical + marketplace). Those files are Keith's plugin lane; this rubric VERIFIES nothing about them (we consume the contract — handler shape — they own).
- **`daydream.precompact_skipped_stop_running` event** (ADR-017 open item; engine-only change, not CLI/handler).
- **Transcript-path hardening** (ADR-017 carve-out — symlink resolution, path-prefix allowlist).
- **Night-dream worker body.** `DreamingWorker.run` still raises `NotImplementedError`; `daydream-cli dream --all` still catches it and fail-opens.
- **Deletion of the legacy `eval/memeval/claudecode/plugin/` tree.** Banner-only in this PR; deletion deferred until green bench (per user).
- **ADR-001 amendment recording the subprocess-shell-out shape.** Text-only successor PR.
- **Async-Stop timeout calibration beyond 600s.** If 600s is empirically too short, raise in a follow-up.
- **Plugin-CLI version-skew protection.** The handler invokes `daydream-cli` by bare name; no minimum-version check. Mitigated by the `daydream.cli_resolved` event (PR5 criterion 36).
- **CHANGELOG / README writeup of the migration.** Code-is-the-source-of-truth; the three-change diff is the durable record.

---

**Pass condition:** every box checked. Any FAIL or any unchecked-without-N-A-justification = NOT DONE; the work is not ready for merge.
