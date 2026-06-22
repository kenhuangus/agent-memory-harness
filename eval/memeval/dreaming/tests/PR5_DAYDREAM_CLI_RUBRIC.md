# PR5 — `daydream-cli` console script + Claude Code Stop/PreCompact plugin hooks — done rubric

Implementer checks each item off before marking PR ready for review. Jasnah re-verifies on review. Anything fuzzy or unchecked = NOT DONE. Each criterion is boolean (PASS / FAIL / N-A). No partial credit. No compounds — every "and" is split into its own line.

Scope: PR5 only. The two coupled deliverables governed by ADR-dreaming-016 + ADR-dreaming-001:

1. `eval/memeval/dreaming/cli.py` — a new standalone argparse-driven console script with two subcommands (`daydream` and `dream --all`), registered as the `daydream-cli` entry point in `eval/pyproject.toml`.
2. `eval/memeval/claudecode/plugin/.claude-plugin/plugin.json` — a new `hooks` block wiring `Stop` (`async: true`) and `PreCompact` (synchronous, matcher `manual|auto`) to a `daydream-cli daydream` invocation. The CLI reads `session_id` and `transcript_path` from a stdin JSON object per the Claude Code plugin hooks contract (https://code.claude.com/docs/en/hooks, fetched 2026-06-21). `--session` and `--log` flags remain available as overrides for manual/test invocation but are NOT required when stdin contains a hook payload.

NOT in scope (explicitly carved out — see §X for the full "not gated" list): the real night-Dream consolidation engine (`worker.py` remains a stub; the CLI's `dream --all` subcommand catches `NotImplementedError` and fail-opens to exit 0), stale-lock 24h reclaim, `LocalClient`/`AnthropicClient` real implementations, audit-file retention beyond the 30-day TTL already shipped in PR4, log-adapter abstraction (ADR-harness-005), chunking with overlap.

Assumption: PR1 + PR2 + PR3 + PR4 are merged and green. Specifically: `daydream(*, session_id, log_path, store, client=None, basedir=None, now=None, id_gen=None) -> None` is importable from `memeval.dreaming.engine`; `DreamingWorker.run` raises `NotImplementedError`; the `daydream` extra in `eval/pyproject.toml` exists and pulls `detect-secrets==1.5.0` + `httpx>=0.27`. Any of those failing red-pages this rubric.

Anchors:
- ADR-001 = `docs/adrs/ADR-dreaming-001-daydreaming-stop-fired.md`
- ADR-008 = `docs/adrs/ADR-dreaming-008-memory-cli-console-script.md` (superseded by ADR-016 — carried-forward decisions still bind)
- ADR-016 = `docs/adrs/ADR-dreaming-016-rename-memory-cli-to-daydream-cli.md`
- ADR-harness-006 = `docs/adrs/ADR-harness-006-fail-open.md`
- ADR-storage-001 = `docs/adrs/ADR-storage-001-orchestrator-in-process-library.md`
- ADR-015 = `docs/adrs/ADR-dreaming-015-filesystem-state-management.md` (the `MEMORY_STORE` env-var rule — only non-fail-open exit path the engine has)
- arch §3 = `architecture.md` §3 (offline-imports rule)

Per-PR judgment calls (recorded once, not re-litigated per criterion):
- **CLI entry point name** is `daydream-cli` per ADR-016 §Decision. The bare `memory` name from superseded ADR-008 must NOT appear in `[project.scripts]` after this PR — criterion 6 enforces.
- **Subcommand pattern** is `daydream-cli daydream …` and `daydream-cli dream --all …` per ADR-016 §Consequences "Shape — invocation." The redundancy of `daydream-cli daydream` is intentional (ADR-016 §Rationale) — implementer must NOT collapse to a single-level command.
- **`dream --all` carve-out.** `eval/memeval/dreaming/worker.py` raises `NotImplementedError` (untouched by PR5). The CLI subcommand for night-dream must catch *only* `NotImplementedError` and exit 0; any other exception path is governed by the engine-level fail-open contract in ADR-harness-006 and is also exit 0, but the carve-out point (catching `NotImplementedError` specifically) is what makes the CLI shape contract-complete while the engine is stubbed. Criteria 49–55 split this.
- **`--store` semantics.** When `--store P` is supplied, the CLI threads `P` to the engine via `os.environ["MEMORY_STORE"]` (env-var threading; option (a)). This keeps ADR-015 as the single source of truth for store-resolution. The CLI MUST restore the prior `MEMORY_STORE` value (or unset if not previously set) in a `try/finally` so subsequent processes in the same shell see the original. Single option; no implementer choice.
- **Plugin manifest hook shape.** Claude Code plugin hooks are an external contract surface (Anthropic CC plugin spec — https://code.claude.com/docs/en/hooks, fetched 2026-06-21). The `command` string is `daydream-cli daydream` verbatim — `session_id` and `transcript_path` arrive via **stdin JSON**, NOT env vars, NOT argv interpolation. Criteria 56–75 pin the literal shape. Both hook entries are sha256-pinned (criteria 74–75) to lock against silent drift.
- **Argparse error exit code is `1` (NOT 2).** CC's plugin-hooks spec reserves exit code 2 as "blocking error" — exit 2 from a Stop hook prevents Claude from stopping; exit 2 from PreCompact blocks compaction. Argparse's stdlib default is exit 2; the CLI MUST override (via `ArgumentParser(exit_on_error=False)` + a try/except SystemExit wrapper at the `main()` boundary, OR by subclassing `ArgumentParser.error` to call `sys.exit(1)`). This supersedes ADR-008's "exit codes: 2 = argument error" — a follow-up text-only ADR-008/016 amendment is tracked in §X.
- **Why `KeyboardInterrupt` / `SystemExit` propagate.** Criteria 42, 43, 55 enforce that the CLI's outer `except` clause uses `Exception` (not `BaseException`). Rationale: keeps `main()` testable in-process (`pytest --capture=no` cancel works) and gives the synchronous PreCompact invocation a clean cancel path. The async Stop invocation rarely encounters either at runtime but the contract is uniform for testability.

---

## A. `[project.scripts]` registration & install surface

- [ ] 1. `eval/pyproject.toml` `[project.scripts]` table contains the literal line `daydream-cli = "memeval.dreaming.cli:main"` — ADR-016 §Consequences "Shape" — `test_pyproject_registers_daydream_cli` (parses pyproject; asserts exact key + value).
- [ ] 2. `eval/pyproject.toml` `[project.scripts]` does NOT contain a `memory =` entry (the superseded ADR-008 name) — ADR-016 §Decision (rename, not addition) — `test_pyproject_does_not_register_memory_cli`.
- [ ] 3. `eval/pyproject.toml` `[project.scripts]` still contains `memeval = "memeval.cli:main"` (unchanged by PR5) — ADR-016 §Consequences "Shape" — `test_pyproject_memeval_entry_unchanged`.
- [ ] 4. `eval/pyproject.toml` `[project.scripts]` still contains `memeval-bench = "memeval.claudecode.run_bench:main"` (unchanged by PR5) — scope discipline — `test_pyproject_memeval_bench_entry_unchanged`.
- [ ] 5. After `pip install -e eval[daydream]` in a clean venv, the shell command `daydream-cli --help` exits 0 and stdout contains the literal substrings `daydream` AND `dream` (both subcommands listed) — ADR-016 §Consequences "Shape — invocation" — manual: implementer records the command + exit code + stdout in the PR description; reviewer re-runs.
- [ ] 6. Without the `daydream` extra (bare `pip install -e eval`), the shell command `daydream-cli --help` still resolves on `$PATH` (the script wrapper exists; the import-detect-secrets failure is the lazy-load concern of PR1, not PR5) — ADR-008 §Consequences "Policy — `daydream` extra is required" (carried forward by ADR-016) — `test_daydream_cli_script_resolves_without_extra` (subprocess: `shutil.which("daydream-cli")` is non-None after `pip install -e .` of the eval package; full subcommand execution is NOT asserted at this criterion — that's criterion 7).
- [ ] 7. Without the `daydream` extra installed, `daydream-cli daydream --session s --log /tmp/x` exits non-zero with a stderr message naming the `daydream` extra (graceful "install the extra" error, not a raw `ModuleNotFoundError` traceback) — ADR-008 §Consequences "Policy — `daydream` extra is required" — `test_daydream_cli_without_extra_emits_actionable_error` (subprocess; uninstalls `detect-secrets` first; asserts exit code != 0; asserts stderr contains the literal substring `daydream` AND `pip install` OR `extra`).

## B. Module shape & public surface

- [ ] 8. `eval/memeval/dreaming/cli.py` exists and defines a top-level `main` callable — ADR-008 §Decision (carried forward by ADR-016) — `test_cli_module_exists`.
- [ ] 9. `main`'s signature is exactly `main(argv: list[str] | None = None) -> int` (argv-injectable for in-process tests; returns the integer exit code) — slop-detection (the alternative — `main()` calling `sys.exit()` directly — is untestable in-process) — `test_main_signature_is_argv_in_int_out` (`inspect.signature`).
- [ ] 10. `main()` invoked with no args is equivalent to `main(sys.argv[1:])` — convention — `test_main_defaults_to_sys_argv`.
- [ ] 11. `eval/memeval/dreaming/cli.py` does NOT import `engine`, `_state`, `_extract`, or `redaction` at module top (heavy deps stay lazy per arch §3). These imports MUST happen inside `main()` (or inside a subparser's handler function) BEFORE the `try:` block that absorbs engine exceptions — a missing-engine import must surface as a visible ImportError to the implementer, NOT be silently swallowed by the fail-open. The import MUST NOT be inside an `except:` block. AST-scan + a positive test that runs `main(["daydream", "--help"])` after `del sys.modules['memeval.dreaming.engine']` and asserts the import is re-attempted — `test_cli_module_stdlib_only_at_top` (AST: top-level imports allowlist: `argparse`, `sys`, `pathlib`, `logging`, `os`, `json`, `__future__`) and `test_cli_imports_engine_inside_main_before_try` (AST: locate the import-engine statement; assert its parent `FunctionDef` is `main` or a handler; assert it appears before the first `Try` node that catches `Exception`).
- [ ] 12. `import memeval.dreaming.cli` does NOT cause `detect_secrets` to be imported into `sys.modules` — arch §3 + lazy-import rule from PR1 §E — `test_cli_import_does_not_load_detect_secrets` (subprocess Python: `import memeval.dreaming.cli; assert 'detect_secrets' not in sys.modules`).
- [ ] 13. `import memeval.dreaming.cli` does NOT cause `httpx` to be imported into `sys.modules` — arch §3 + PR4 criterion 171 lineage — `test_cli_import_does_not_load_httpx` (subprocess Python; assertion).

## C. Argparse top-level shape

- [ ] 14. `main(["--help"])` exits 0 (stdlib argparse convention) — slop-detection — `test_main_help_exits_zero`.
- [ ] 15. `main([])` (no subcommand) exits **1** with a stderr message naming the available subcommands — CC reserves exit 2; see preamble — `test_main_no_subcommand_exits_one`.
- [ ] 16. `main(["bogus-subcommand"])` exits **1** — CC reserves exit 2; see preamble — `test_main_bogus_subcommand_exits_one`.
- [ ] 17. `main(["daydream", "--unknown-flag"])` exits **1** — CC reserves exit 2; see preamble — `test_main_unknown_flag_exits_one`.
- [ ] 18. `cli.py` never calls `sys.exit(2)` and never returns the integer `2` from `main` — CC reserves exit 2 for "blocking error" (Stop hook: prevents Claude from stopping; PreCompact: blocks compaction) — `test_cli_never_exits_with_code_two` (AST scan of `cli.py` for `Call(func=Attribute(attr="exit"), args=[Constant(2)])` and `Return(value=Constant(2))`; assert zero matches).
- [ ] 19. The argparse top-level parser registers EXACTLY TWO subcommands: `daydream` and `dream` — ADR-016 §Consequences "Shape — invocation" — `test_main_registers_exactly_two_subcommands` (introspects the configured parser via a test-only `_build_parser()` helper, or parses `--help` output for the subcommand block).
- [ ] 20. The argparse top-level parser's program name (`prog=`) is `daydream-cli` (so `--help` output reads `usage: daydream-cli …`, not `usage: cli.py …` or `usage: __main__.py …`) — ADR-016 §Consequences "Shape — invocation" — `test_main_prog_name_is_daydream_cli`.

## D. `daydream` subcommand — argparse surface

- [ ] 21. `daydream-cli daydream --help` exits 0 — slop-detection — `test_daydream_subcommand_help_exits_zero`.
- [ ] 22. `daydream-cli daydream` with NO flags AND empty/missing stdin exits 0 fail-open (not 2; not 1; see criterion 28 for the bad-stdin contract) — CC reserves exit 2; missing-input is a fail-open case for the plugin path — `test_daydream_subcommand_no_input_failopens_zero`.
- [ ] 23. `--session` is OPTIONAL at the argparse layer (was REQUIRED). The CLI requires *either* `--session` *or* a `session_id` key in stdin JSON; absence of both is a fail-open WARNING, not an argparse error — preamble (stdin JSON path) — `test_daydream_session_optional_when_stdin_provides`.
- [ ] 24. `--log` is OPTIONAL at the argparse layer (was REQUIRED). The CLI requires *either* `--log` *or* a `transcript_path` key in stdin JSON; absence of both is a fail-open WARNING — preamble — `test_daydream_log_optional_when_stdin_provides`.
- [ ] 25. When `--log` IS supplied, the parsed namespace's `.log` attribute is a `pathlib.Path` instance — slop-detection — `test_daydream_log_is_pathlike_when_supplied`.
- [ ] 26. `--store` is OPTIONAL and coerced to `pathlib.Path` when present — ADR-001 §Consequences "Shape" — `test_daydream_store_optional_pathlike`.
- [ ] 27. `daydream-cli daydream --session SID --log /tmp/x` (no `--store`) is accepted (argparse exit 0 on parse — the engine's `MEMORY_STORE` resolution path covers the resolution semantics, NOT this CLI parser) — ADR-016 §Consequences "Shape — invocation" + ADR-015 §1 — `test_daydream_no_store_parses_clean`.
- [ ] 28. `daydream-cli daydream` with NO flags + stdin containing valid hook JSON (`{"session_id": "S", "transcript_path": "/tmp/log", "hook_event_name": "Stop"}`) calls `engine.daydream(session_id="S", log_path=Path("/tmp/log"), …)` exactly once — CC plugin hooks contract — `test_daydream_reads_stdin_json_when_no_flags` (monkeypatch `sys.stdin` to a StringIO; monkeypatch `engine.daydream`; assert call kwargs).
- [ ] 29. Explicit `--session SID --log /tmp/x` flags OVERRIDE any stdin JSON values for the same fields when both are present — preamble — `test_daydream_flags_override_stdin_json`.
- [ ] 30. Stdin that is empty, non-JSON, or missing required keys (`session_id` AND `transcript_path` both absent), with no `--session`/`--log` overrides, causes `main` to return `0` and emit a WARNING-or-higher log record naming `stdin` — preamble + ADR-harness-006 — `test_daydream_failopens_on_bad_stdin`.
- [ ] 31. When stdin JSON contains a `hook_event_name` key, the CLI threads its value (a string) into the `daydream.cli_resolved` event under field `hook_event_name` (see criterion 36) — F4 — `test_daydream_records_hook_event_name`.

## E. `daydream` subcommand — engine wiring

- [ ] 32. `daydream-cli daydream --session SID --log <real-log-file>` calls `memeval.dreaming.engine.daydream` exactly once with keyword args `session_id="SID"` AND `log_path=Path("<real-log-file>")` — ADR-001 §Consequences "Shape" — `test_daydream_subcommand_invokes_engine` (monkeypatch `engine.daydream` to record kwargs; in-process `main([...])`; assert call args). NOTE: this is an in-process test, NOT a subprocess test — that's criterion 42.
- [ ] 33. When `--store P` is supplied, the CLI sets `os.environ["MEMORY_STORE"] = str(Path(P).resolve())` for the duration of the `engine.daydream` call, AND restores the prior value (or unsets it if not previously set) in a `try/finally` block — preamble (`--store` semantics) + ADR-015 §1 — `test_daydream_threads_store_via_env_var_with_restore` (in-process; capture `os.environ` state before, during (via a monkeypatched engine that reads env), and after; assert (a) `MEMORY_STORE == str(Path(P).resolve())` at call time, (b) prior value is restored after, (c) unset state is restored if it was unset before).
- [ ] 34. The CLI passes through the Orchestrator (`memeval.stores.orchestrator` or whatever the storage-domain ships) as the `store=` argument to `engine.daydream`, NOT a raw `MemoryStore` instance — ADR-001 §Consequences "Policy" ("writes go through the Orchestrator") + ADR-storage-001 — `test_daydream_subcommand_uses_orchestrator` (monkeypatch the orchestrator factory; assert it was called once and its return value was passed as `store=` to `engine.daydream`). NOTE: if the Orchestrator's factory name/path is not yet final at PR5 merge, the implementer documents the chosen factory in `cli.py`'s module docstring and the test imports that symbol — but the test must exist and assert routing.
- [ ] 35. On a successful `engine.daydream` return (no exception), the CLI's `main` returns `0` — ADR-008 §Consequences "Exit codes: 0 = success" — `test_daydream_subcommand_exits_zero_on_success` (monkeypatch `engine.daydream` to return `None`; assert `main(["daydream", "--session", "s", "--log", "/tmp/x"]) == 0`).
- [ ] 36. On every `daydream-cli daydream` invocation that proceeds to call `engine.daydream`, the CLI emits exactly one `daydream.cli_resolved` event (via the PR3 events shim) containing exactly these fields: `sys_executable: str`, `script_path: str` (resolved absolute path of `sys.argv[0]`), `package_version: str` (from `importlib.metadata.version("memeval")`), `engine_module_path: str` (resolved file path of the imported `engine` module), `hook_event_name: str | None` (from stdin JSON if present, else `None`) — F4 (PATH stale binaries + version observability) — `test_daydream_emits_cli_resolved_event` (monkeypatch the events `emit` callable; assert exactly one call with the named event; assert kwargs dict has these five keys with these types).
- [ ] 37. Subprocess test: `daydream-cli daydream --session "../../etc/passwd" --log /tmp/fake.log` with `MEMORY_STORE` pointing at a real temp dir exits 0 (fail-open) AND no file is written outside `MEMORY_STORE`'s parent directory. The CLI passes the raw `session_id` through unchanged; sanitization happens inside the engine's `safe_session_stem` — F8 — `test_daydream_passes_session_id_raw_to_engine_subprocess` (subprocess; before/after directory scan; assert no new files outside the tempdir).

## F. `daydream` subcommand — fail-open contract (ADR-harness-006)

The engine's fail-open contract guarantees `engine.daydream` returns `None` rather than raising — for everything *except* `resolve_basedir`-style configuration errors (ADR-015 §1: KeyError / FileNotFoundError / ValueError on a misconfigured `MEMORY_STORE`). The CLI's contract is: it must absorb those configuration exceptions too (turn them into exit 0 + a logged warning), because the plugin shell-out from Claude Code's Stop hook must never surface a non-zero exit. The criteria below split each exception class.

- [ ] 38. When `engine.daydream` raises `KeyError` (unset `MEMORY_STORE` and no `--store` provided), `main` returns `0` and a WARNING-or-higher log record is emitted naming `MEMORY_STORE` — ADR-015 §1 + ADR-harness-006 — `test_daydream_subcommand_failopens_on_keyerror` (monkeypatch `engine.daydream` to raise `KeyError("MEMORY_STORE")`; assert exit 0; use `caplog`).
- [ ] 39. When `engine.daydream` raises `FileNotFoundError` (`MEMORY_STORE` path missing), `main` returns `0` and a WARNING-or-higher log record is emitted naming the missing path — ADR-015 §1 + ADR-harness-006 — `test_daydream_subcommand_failopens_on_filenotfounderror`.
- [ ] 40. When `engine.daydream` raises `ValueError` (`MEMORY_STORE` points at a directory), `main` returns `0` and a WARNING-or-higher log record is emitted — ADR-015 §1 + ADR-harness-006 — `test_daydream_subcommand_failopens_on_valueerror`.
- [ ] 41. When `engine.daydream` raises any other `Exception` subclass (defense-in-depth — should never happen given the engine's own fail-open boundary, but the CLI absorbs it too), `main` returns `0` and a WARNING-or-higher log record is emitted with the exception class name in the message — ADR-harness-006 (engine + CLI BOTH fail-open) — `test_daydream_subcommand_failopens_on_unexpected_exception` (monkeypatch `engine.daydream` to raise `RuntimeError("boom")`; assert exit 0; assert log record contains `RuntimeError`).
- [ ] 42. `KeyboardInterrupt` raised by `engine.daydream` PROPAGATES (the CLI must NOT swallow it — mirrors PR4 criterion 166) — slop-detection — `test_daydream_subcommand_does_not_swallow_keyboardinterrupt` (monkeypatch to raise `KeyboardInterrupt`; assert `main(...)` re-raises).
- [ ] 43. `SystemExit` raised inside `engine.daydream` PROPAGATES (the CLI's outer `except Exception` clause must use `Exception` literally, not bare `except:` — mirrors PR1 criterion 36 + PR4 criterion 146) — slop-detection — `test_daydream_subcommand_does_not_swallow_systemexit`.
- [ ] 44. The CLI's fail-open `except` clause catches `Exception` (not `BaseException`, not bare `except:`) — AST-scan of `cli.py` — `test_cli_failopen_catches_exception_not_bare` (AST: every `ExceptHandler` whose body contains an early-return-zero or `return 0` has `type=Name("Exception")`, never `None` and never `Name("BaseException")`).

## G. `dream --all` subcommand — argparse surface

- [ ] 45. `daydream-cli dream --help` exits 0 — slop-detection — `test_dream_subcommand_help_exits_zero`.
- [ ] 46. `daydream-cli dream` (missing `--all`) exits **1** — CC reserves exit 2; see preamble — `test_dream_subcommand_requires_all_flag_exits_one`.
- [ ] 47. `daydream-cli dream --all` accepts the optional `--store P` flag (same `Path` coercion as `daydream --store`) — ADR-016 §Consequences "Shape — invocation" — `test_dream_subcommand_store_optional_pathlike`.
- [ ] 48. `--all` is a boolean flag (no value), and it is REQUIRED (no implicit default) — ADR-008 §Consequences "Shape" (carried forward) — `test_dream_subcommand_all_is_required_boolean`.

## H. `dream --all` subcommand — fail-open carve-out (`worker.py` stub)

`worker.py` is byte-identical to PR4 (raises `NotImplementedError` from `DreamingWorker.run`). PR5 wires the CLI shape but the engine is stubbed; the carve-out is: catch `NotImplementedError` specifically, exit 0 with an explicit "night-dream not yet implemented" log line, so a future `worker.py` implementation can be dropped in without touching the CLI.

- [ ] 49. `daydream-cli dream --all` returns `0` even though `DreamingWorker.run` raises `NotImplementedError` — PR5 carve-out — `test_dream_all_failopens_on_notimplementederror` (subprocess OR in-process; assert exit 0).
- [ ] 50. When `DreamingWorker.run` raises `NotImplementedError`, the CLI emits the event `daydream.dream_all_skipped` (literal event-name string, pinned verbatim) — F9 — `test_dream_all_emits_skipped_event` (monkeypatch the events `emit` callable; assert exactly one call with the pinned event name).
- [ ] 51. When `DreamingWorker.run` raises `NotImplementedError`, the CLI ALSO emits a WARNING-or-higher log record whose message contains the literal substring `night` OR `consolidation` (so the no-op is visible in logs, not silent) — slop-detection — `test_dream_all_logs_notimplemented_visibly` (in-process; `caplog`).
- [ ] 52. When `DreamingWorker.run` raises ANY OTHER exception (e.g. a future bug after the stub is replaced), `main` returns `0` AND the CLI emits the event `daydream.dream_all_error` (literal event-name string, pinned verbatim) AND logs a WARNING naming the exception class — F9 + ADR-harness-006 — `test_dream_all_failopens_and_emits_error_event` (monkeypatch the events `emit`; assert exit 0; assert event name; assert exception class in log message).
- [ ] 53. `worker.py` is byte-identical to its pre-PR5 contents (PR5 does NOT touch night Dream) — scope discipline — `test_worker_unchanged` (diff PR5's branch vs `main` for `worker.py`; expect zero lines changed).
- [ ] 54. The CLI threads `--store P` through to the night-dream entrypoint the same way as criterion 33 (env-var threading with try/finally restore) — convention — `test_dream_all_threads_store_arg`.
- [ ] 55. `KeyboardInterrupt` raised by `DreamingWorker.run` PROPAGATES out of `dream --all` — mirrors criterion 42 — `test_dream_all_does_not_swallow_keyboardinterrupt`.

## I. Claude Code plugin manifest — `hooks` block shape

The plugin manifest is the external-contract surface that fires Daydream. Every field is checked verbatim because the manifest is consumed by the Claude Code runtime — typos do not raise, they silently no-op. Reference: https://code.claude.com/docs/en/hooks (fetched 2026-06-21).

The shape per the CC plugin hooks spec:
```json
{
  "hooks": {
    "Stop": [
      {"hooks": [{"type": "command", "command": "daydream-cli daydream", "async": true, "timeout": 600}]}
    ],
    "PreCompact": [
      {"matcher": "manual|auto", "hooks": [{"type": "command", "command": "daydream-cli daydream", "timeout": 600}]}
    ]
  }
}
```

`session_id` and `transcript_path` arrive via stdin JSON (NOT env-var, NOT argv interpolation). The `command` field has NO placeholders.

- [ ] 56. `eval/memeval/claudecode/plugin/.claude-plugin/plugin.json` is valid JSON (loads via `json.loads(Path(...).read_text())`) — slop-detection — `test_plugin_manifest_is_valid_json`.
- [ ] 57. `manifest["hooks"]` is a dict — CC plugin spec — `test_plugin_manifest_has_hooks_block`.
- [ ] 58. `manifest["hooks"]["Stop"]` is a list AND every element of the list is a dict — CC plugin spec (Stop is a list of hook-group dicts) — `test_plugin_manifest_stop_is_list_of_dicts`.
- [ ] 59. `manifest["hooks"]["Stop"]` has exactly ONE element (one hook group) — scope discipline — `test_plugin_manifest_stop_has_single_hook_group`.
- [ ] 60. `manifest["hooks"]["Stop"][0]["hooks"]` is a list with EXACTLY ONE entry — scope discipline — `test_plugin_manifest_stop_has_single_hook`.
- [ ] 61. That single Stop hook entry has `"type": "command"` (literal string match) — CC plugin spec — `test_plugin_manifest_stop_hook_type_is_command`.
- [ ] 62. That single Stop hook entry's `"command"` field equals the literal string `"daydream-cli daydream"` — CC plugin spec + ADR-016 §Consequences "Shape — invocation" — `test_plugin_manifest_stop_command_is_daydream_cli_daydream` (assert `entry["command"] == "daydream-cli daydream"`).
- [ ] 63. That single Stop hook entry's `"async"` field is the literal JSON boolean `true` (not the string `"true"`) — ADR-001 §Decision — `test_plugin_manifest_stop_hook_async_is_true` (assert `entry["async"] is True`).

> **Why `async: true` on Stop is correct (closes a recurring adversarial misread).** Per https://code.claude.com/docs/en/hooks (verified 2026-06-21), `async` is a common field on all command hooks with no event-specific restriction. The doc table reads: `async | no | If true, runs in the background without blocking.` The semantic consequence — async hooks cannot use exit code 2 to block, because CC does not wait for them — is the EXACT property PR5 wants: Daydreaming is fire-and-forget; Claude must finish stopping without waiting for the LLM extraction pass. Blocking would defeat ADR-001's "automatic in-session memory capture" intent. (If a future review wants to wake Claude on extraction failure, the spec's separate `asyncRewake` field carries that contract; v1 does not opt in.)

- [ ] 64. That single Stop hook entry has a `"timeout"` field whose value is a positive integer — CC plugin spec (per-hook timeout; doc example is 600s) — `test_plugin_manifest_stop_has_positive_timeout` (assert `isinstance(entry["timeout"], int) and entry["timeout"] > 0`).
- [ ] 65. `manifest["hooks"]["PreCompact"]` is a list AND every element is a dict — CC plugin spec — `test_plugin_manifest_precompact_is_list_of_dicts`.
- [ ] 66. `manifest["hooks"]["PreCompact"]` has exactly ONE hook-group element — scope discipline — `test_plugin_manifest_precompact_has_single_hook_group`.
- [ ] 67. The PreCompact hook-group's `"matcher"` field is either the literal string `"manual|auto"` OR absent (both valid "catch-all" per CC plugin spec) — CC plugin spec — `test_plugin_manifest_precompact_matcher_shape` (assert `group.get("matcher", "manual|auto") == "manual|auto"`).
- [ ] 68. The PreCompact group's inner `"hooks"` list has exactly ONE entry whose `"command"` equals the literal string `"daydream-cli daydream"` (matches Stop) — CC plugin spec + ADR-016 — `test_plugin_manifest_precompact_command_matches_stop`.
- [ ] 69. The PreCompact inner hook entry does NOT carry `"async": true` — ADR-001 §Decision ("final pre-compaction pass" is synchronous) — `test_plugin_manifest_precompact_is_synchronous` (assert `entry.get("async", False) is False`).
- [ ] 70. The PreCompact inner hook entry has a `"timeout"` field whose value is a positive integer — CC plugin spec — `test_plugin_manifest_precompact_has_positive_timeout`.
- [ ] 71. Across BOTH hooks, the literal `"command"` string contains NONE of: `$`, `${`, `{{`, `$CLAUDE_SESSION_ID`, `$CLAUDE_TRANSCRIPT_PATH`, `--session`, `--log` — CC plugin spec does NOT interpolate session-data into `command`; including a placeholder would be passed verbatim to the shell and silently fail — `test_plugin_manifest_command_has_no_session_interpolation` (substring scan over both command strings; assert none of the forbidden tokens appear).
- [ ] 72. The manifest's top-level `name` field is unchanged from pre-PR5 (`"memeval-memory"`) — scope discipline — `test_plugin_manifest_name_unchanged`.
- [ ] 73. The manifest's top-level `version` field is bumped from its pre-PR5 value (a hooks-block addition is a behavioral change deserving a SemVer bump) — convention — `test_plugin_manifest_version_bumped` (compare to `main`'s value via git-show).
- [ ] 74. The Stop hook entry serialized via `json.dumps(manifest["hooks"]["Stop"][0]["hooks"][0], sort_keys=True)` sha256-hashes to the value pinned literally in the test source — slop-detection (locks the invocation contract against silent drift) — `test_plugin_manifest_stop_hook_sha256_pinned`. The pinned hash is a literal string constant in the test file, computed once at commit time from the canonical manifest (NOT regenerated by the test on every run, which would defeat the drift-detection); the test ONLY recomputes the manifest's hash and asserts equality with the constant.
- [ ] 75. The PreCompact hook entry serialized the same way sha256-hashes to its own pinned value (also a literal string constant in the test file, computed once at commit time) — slop-detection — `test_plugin_manifest_precompact_hook_sha256_pinned`.

## J. Plugin manifest distribution surface

- [ ] 76. The manifest file at `eval/memeval/claudecode/plugin/.claude-plugin/plugin.json` is included in the wheel via `[tool.setuptools.package-data]` (the existing `"memeval.claudecode" = [..., "plugin/.claude-plugin/*.json"]` line covers it) — `test_plugin_manifest_in_package_data` (parses pyproject; asserts the existing pattern still matches the new manifest path).
- [ ] 77. `python -c 'from importlib.resources import files; print(files("memeval.claudecode").joinpath("plugin/.claude-plugin/plugin.json").read_text())'` exits 0 and prints the manifest's `hooks` block AFTER `pip install -e eval` — distribution floor — manual: implementer records command + output in PR description.

## K. End-to-end shell smoke

Minimum-viable end-to-end checks the rubric considers the verification floor for PR5. All manual (subprocess-driven from a clean venv); implementer records exact commands + exit codes + log snippets in the PR description for the reviewer to re-run.

- [ ] 78. **E2E happy path** — manual:
  1. `pip install -e eval[daydream]` in a clean venv.
  2. `export MEMORY_STORE=$(mktemp -d)/store.jsonl && touch $MEMORY_STORE`
  3. Create a fake transcript: `printf 'session start\nuser: hello\n' > /tmp/fake-session.log`
  4. Run: `daydream-cli daydream --session smoke --log /tmp/fake-session.log`
  5. Expected: exit 0; the sidecar file `$(dirname $MEMORY_STORE)/dream/smoke.json` is created; the diary file `$(dirname $MEMORY_STORE)/dream/smoke.daydream-events.jsonl` contains at least one event.
  Implementer records: the four files' presence + sizes + the actual exit code.
- [ ] 79. **E2E fail-open path (flag invocation)** — manual:
  1. Same venv as criterion 78.
  2. `unset MEMORY_STORE`
  3. Run: `daydream-cli daydream --session smoke --log /tmp/fake-session.log`
  4. Expected: exit 0 (NOT non-zero); a log message naming `MEMORY_STORE` on stderr.
  Implementer records exit code + the relevant stderr line.
- [ ] 80. **E2E PreCompact-shape stdin fail-open path** — manual:
  1. Same venv as criterion 78.
  2. `unset MEMORY_STORE`
  3. Pipe a PreCompact-shape stdin payload: `echo '{"session_id":"smoke","transcript_path":"/tmp/fake-session.log","hook_event_name":"PreCompact"}' | daydream-cli daydream`
  4. Expected: exit 0; the fail-open log line includes the literal substring `hook_event_name=PreCompact` (or equivalent structured form via the events shim).
  Implementer records exit code + log line.
- [ ] 81. **E2E `dream --all` carve-out** — manual:
  1. Same venv.
  2. Run: `daydream-cli dream --all`
  3. Expected: exit 0; a log message containing `night` or `consolidation` indicating the no-op.
  Implementer records exit code + the relevant log line.

## L. Anti-slop (deterministic source-scan)

- [ ] 82. Zero `TODO`/`FIXME`/`XXX`/`HACK` comments in `eval/memeval/dreaming/cli.py` — slop-detection (mirrors PR4 criterion 140) — `test_no_todo_markers_in_pr5_modules`.
- [ ] 83. Zero `print()` statements in `cli.py` (logging only — log messages must be picked up by `caplog` in the fail-open tests) — slop-detection (mirrors PR4 criterion 141) — `test_no_print_calls_in_cli` (AST scan).
- [ ] 84. Zero `sys.exit()` calls in the body of `main` (the function returns the integer; `sys.exit(main(...))` is the responsibility of the console-script wrapper) — slop-detection (the alternative makes `main` un-testable in-process) — `test_main_does_not_call_sys_exit` (AST scan of `main` and any helper it dispatches to; subparser `set_defaults(func=...)` handlers also must not `sys.exit`). NOTE: the `exit_on_error=False` wrapper of criterion 18 is the mechanism that makes this possible without losing argparse-error handling.
- [ ] 85. Zero `# pragma: no cover` / `# type: ignore` / `# noqa` in `cli.py` unless accompanied by an in-line `# REASON: <text>` justifying it — slop-detection (mirrors PR1 §K criterion 55, PR4 criterion 144) — `test_pragmas_are_justified_in_cli`.
- [ ] 86. Every public function/class in `cli.py` has a one-line docstring naming what it does (`main` at minimum) — slop-detection (mirrors PR4 criterion 145) — `test_public_symbols_have_real_docstrings_pr5`.
- [ ] 87. `cli.py` does NOT use bare `except:` (every `except` clause names an exception class) — slop-detection — `test_no_bare_except_in_cli` (AST scan: every `ExceptHandler.type` is not `None`).
- [ ] 88. `cli.py` does NOT contain stub function bodies (`pass` / `return None` with no other statements) outside `__init__` methods — slop-detection (mirrors PR4 criterion 147) — `test_no_stub_function_bodies_in_cli`.

## M. Test-suite hygiene (the rubric IS the test plan)

- [ ] 89. Every test named in this rubric exists as a function in one of: `eval/memeval/dreaming/tests/test_cli.py`, `eval/memeval/dreaming/tests/test_plugin_manifest.py` — slop-detection (mirrors PR1 criterion 67, PR4 criterion 155) — `test_rubric_test_names_all_present_pr5` (introspects collected pytest items; asserts every test-function name in this rubric file appears in the collected set).
- [ ] 90. `pytest eval/memeval/dreaming/tests/test_cli.py eval/memeval/dreaming/tests/test_plugin_manifest.py` exits 0 on a clean checkout with `pip install -e eval[daydream]` — verification floor — manual: implementer records the command + exit code in the PR description; reviewer re-runs.
- [ ] 91. PR1–PR4's existing tests all still pass (no regressions). Specifically: `pytest eval/memeval/dreaming/tests/` exits 0 — verification floor — manual + CI.
- [ ] 92. PR5's diff touches ONLY paths under: `eval/memeval/dreaming/cli.py`, `eval/memeval/dreaming/tests/test_cli.py`, `eval/memeval/dreaming/tests/test_plugin_manifest.py`, `eval/memeval/claudecode/plugin/.claude-plugin/plugin.json`, `eval/pyproject.toml` (the new `[project.scripts]` line). The rubric file (`PR5_DAYDREAM_CLI_RUBRIC.md`) is NOT in this set — it lands in the prerequisite PR (`dreaming/pr5-prereq-adrs`) alongside the ADR-017 + ADR-018 amendments. No edits to `engine.py`, `_state.py`, `_extract.py`, `prompts.py`, `worker.py`, `events.py`, `llm.py`, `redaction/`, `schema.py`, `protocols.py`, `harness.py` — scope discipline — manual: `git diff --name-only main...HEAD | sort -u` audited by reviewer.
- [ ] 93. PR5's pull-request description contains a section titled `Known limitation` (or `Known limitations`) that explicitly names the PreCompact-skip-when-Stop-mid-flight behavior AND links to the ADR-001 amendment commit (or PR) recording the silent-skip — F2 — manual: reviewer verifies the PR body contains both the section heading and a non-broken link.

## N. mypy `--strict` coverage extension

- [ ] 94. `mypy --strict eval/memeval/dreaming/` (the PR4 §138 expanded scope) exits 0 on a clean checkout AFTER `cli.py` is added — ADR-010 §Consequences "Policy" + PR4 criterion 139 — manual: implementer records the command + exit code in the PR description; reviewer re-runs.
- [ ] 95. The mypy override list in `eval/pyproject.toml` (currently exempts `memeval.dreaming.tests.*`, `memeval.dreaming.worker`, `memeval.dreaming.__init__`) does NOT add `memeval.dreaming.cli` (the new module must be policed by `--strict`, not exempted) — slop-detection — `test_cli_not_in_mypy_override_list` (parses pyproject; asserts `memeval.dreaming.cli` is NOT in the module list of the disallow_untyped_defs=false override block).

## O. Rubric adversarial-pass output (mandatory per Jasnah's persona)

Two findings recorded so the dispatcher reviewing this rubric can see them.

- [x] 96. **Adversarial finding #1 — CLOSED 2026-06-21.** Original concern: "the chosen CC-plugin placeholder syntax may not be a real Claude Code plugin runtime feature; the Stop hook would silently no-op." Resolved by fetching https://code.claude.com/docs/en/hooks (2026-06-21) — confirmed: CC does NOT interpolate session-data into the `command` string; data arrives via stdin JSON. Rubric §I rewritten accordingly (criteria 56–75 supersede the original 47–59). Criterion 71 forbids any placeholder syntax in `command`; criteria 74–75 sha256-pin both hook entries to lock against silent drift. NEW RESIDUAL RISK: the CC plugin hooks spec evolves and stdin payload format changes — mitigated by criterion 30 (fail-open on bad/missing stdin) and the sha256 pins.
- [ ] 97. **Adversarial finding #2 — where this rubric might drift from truth.** The rubric was authored from the dispatcher's own framing of PR5 scope. The carve-out boundary between "engine fail-open" (PR4) and "CLI fail-open" (PR5) is asserted twice in places — criteria 38–41 partially duplicate the engine's existing absorption of those exceptions. If the PR4 engine ALREADY absorbs `KeyError` / `FileNotFoundError` / `ValueError` from `resolve_basedir` (it currently does NOT per PR4 criterion 100 — those propagate), criteria 38–40 still hold: the CLI is the shim PR4 explicitly delegated this absorption to. Verified by re-reading PR4 rubric line 100 ("the ONLY non-fail-open path in PR4 ... the plugin/CLI shim in PR5 must wrap the engine call"). No rubric edit required; finding is informational. RUBRIC_GAP: none requiring a structural rewrite.

---

## X. Explicitly NOT gated by PR5 (carved out — do not fail PR5 for missing these)

Reviewers MUST NOT FAIL PR5 for the absence of any of the following. Each is tracked by a different ADR / future PR and is out of PR5 scope by design:

- **Real night-Dream consolidation** (`worker.py` body) — stub remains; `dream --all` catches `NotImplementedError` and exit-0s. Tracked: future PR; ADR-dreaming-002.
- **Stale-lock 24h reclaim** — PR4 carved this out (PR4 plan decision §5(e) Option 1); 30-day TTL applies uniformly to `*.lock`. Tracked: ADR-dreaming-014 Open item.
- **LocalClient / AnthropicClient real implementations** — engine takes any `LLMClient`-protocol-conforming object; OpenRouterClient is the v1 reference impl. Tracked: ADR-dreaming-006.
- **Audit-file retention beyond 30 days** — PR4 ships the 30-day TTL; longer retention plus rotation-fingerprinting is Open. Tracked: ADR-dreaming-013 Open item + ADR-dreaming-011 Open item.
- **Log-adapter abstraction (ADR-harness-005)** — engine reads raw `log_path`. Tracked: ADR-harness-005.
- **Chunking with overlap / `last_summary` feed-forward** — PR4 carved this out. Tracked: ADR-harness-003.
- **`CostTracker` plumbing into `RunResult.cost_usd`** — engine emits `cost_usd` via the diary; roll-up into `RunResult` is Ken's eval-driver lane. Tracked: PR4 plan decision §5(j).
- **ADR-001/002 text updates** to remove the stale `memory daydream` / `memory dream --all` literal references — text-only successor PR. Tracked: ADR-016 Open items.
- **Distribution-channel PATH-collision testing** (pip vs pipx) — manual verification at distribution time, not a code criterion. Tracked: ADR-016 Open items.
- **Replay subcommand (`daydream-cli daydream --replay`)** and **inspect subcommand (`daydream-cli dream --inspect`)** — future additions, not v1. Tracked: ADR-008 Open items (carried forward by ADR-016).
- **mypy `--strict` over `memeval.dreaming.worker`** — `worker.py` is in the override list because its body is a stub. Tracked: removed from override list when the real implementation lands.
- **CHANGELOG / README writeup of the rename** — code-is-the-source-of-truth; ADR-016 is the durable record of the rename. Not a PR5 criterion.
- **PreCompact silent-skip when Stop is mid-flight.** The engine's per-session `flock` (`LOCK_EX | LOCK_NB`) causes a PreCompact-fired daydream to early-return with `_LockHeld` when a Stop-fired daydream is still holding the lock. v1 accepts this silently — data is NOT lost because the cursor is not advanced and the next Stop catches up. A follow-up PR adds an explicit `daydream.precompact_skipped_stop_running` event for operational visibility. Tracked: ADR-dreaming-001 amendment (recorded BEFORE PR5 merges per criterion 93).
- **Transcript-path trust model.** The CLI treats `transcript_path` (from CC stdin) and `--log` (explicit override) as trusted inputs. A hostile transcript path (symlink to `/etc/passwd`, path outside the workspace) is in-scope for the threat model but accepted for v1 personal-machine eval. Hardening (path-prefix allowlist, `O_NOFOLLOW`) deferred. Tracked: new open item in ADR-dreaming-001.
- **Plugin-CLI version-skew protection.** The manifest invokes `daydream-cli` by bare name with no minimum-version check. Users who update the engine without re-installing the plugin (or vice versa) get silent skew. Deferred to first distribution-channel test (per ADR-016 Open items). v1 mitigation: the `daydream.cli_resolved` event (criterion 36) makes the resolved binary path and package version observable in the diary for post-hoc debugging.
- **ADR-008/016 amendment recording the argparse error exit code change** (2 → 1, driven by CC plugin hooks reserving exit 2). Text-only successor PR. Tracked: ADR-016 amendment / new ADR.
- **Concurrent-CLI race on `MEMORY_STORE`.** Criterion 33's env-var threading (`os.environ["MEMORY_STORE"] = str(P)` + try/finally restore) is process-local; safe for the plugin path (one hook → one CLI process). Two concurrent `daydream-cli` invocations in the same shell with different `--store` values would race on `os.environ` because env-var writes are not atomic across threads/forks within the same process. v1 declares this undefined behavior: concurrent CLI invocations with different `--store` values are not supported. The plugin path is single-process per hook invocation, so this is not a v1 risk.

---

**Pass condition:** every box checked. Any FAIL or any unchecked-without-N-A-justification = NOT DONE; the work is not ready for merge.
