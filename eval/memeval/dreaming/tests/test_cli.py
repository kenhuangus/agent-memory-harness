"""PR5 CLI test scaffold — STUBS ONLY.

Each test function corresponds to one criterion in
PR5_DAYDREAM_CLI_RUBRIC.md. Bodies are pytest.skip(...) during the
scaffold phase; the implementation phase replaces each skip with the
real assertion as it lands.
"""

# 63 test stubs across sections §A–§H + §L–§N

from __future__ import annotations

import pytest


# §A — [project.scripts] registration & install surface

def test_pyproject_registers_daydream_cli() -> None:
    """Rubric §A criterion 1 — pyproject [project.scripts] contains daydream-cli = "memeval.dreaming.cli:main"."""
    pytest.skip("PR5 — TODO impl")


def test_pyproject_does_not_register_memory_cli() -> None:
    """Rubric §A criterion 2 — pyproject [project.scripts] has no superseded `memory =` entry."""
    pytest.skip("PR5 — TODO impl")


def test_pyproject_memeval_entry_unchanged() -> None:
    """Rubric §A criterion 3 — pyproject still registers `memeval = "memeval.cli:main"` unchanged."""
    pytest.skip("PR5 — TODO impl")


def test_pyproject_memeval_bench_entry_unchanged() -> None:
    """Rubric §A criterion 4 — pyproject still registers `memeval-bench = "memeval.claudecode.run_bench:main"` unchanged."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_cli_script_resolves_without_extra() -> None:
    """Rubric §A criterion 6 — `daydream-cli` resolves on $PATH even without the `daydream` extra installed."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_cli_without_extra_emits_actionable_error() -> None:
    """Rubric §A criterion 7 — without the `daydream` extra, the CLI exits non-zero with an actionable stderr message naming the extra."""
    pytest.skip("PR5 — TODO impl")


# §B — Module shape & public surface

def test_cli_module_exists() -> None:
    """Rubric §B criterion 8 — eval/memeval/dreaming/cli.py exists and defines a top-level `main` callable."""
    pytest.skip("PR5 — TODO impl")


def test_main_signature_is_argv_in_int_out() -> None:
    """Rubric §B criterion 9 — `main`'s signature is exactly `main(argv: list[str] | None = None) -> int`."""
    pytest.skip("PR5 — TODO impl")


def test_main_defaults_to_sys_argv() -> None:
    """Rubric §B criterion 10 — `main()` with no args is equivalent to `main(sys.argv[1:])`."""
    pytest.skip("PR5 — TODO impl")


def test_cli_module_stdlib_only_at_top() -> None:
    """Rubric §B criterion 11 — cli.py top-level imports are stdlib-only (argparse, sys, pathlib, logging, os, json, __future__)."""
    pytest.skip("PR5 — TODO impl")


def test_cli_imports_engine_inside_main_before_try() -> None:
    """Rubric §B criterion 11 — engine import lives inside `main` (or a handler) and precedes the first `try` catching Exception."""
    pytest.skip("PR5 — TODO impl")


def test_cli_import_does_not_load_detect_secrets() -> None:
    """Rubric §B criterion 12 — importing memeval.dreaming.cli does not pull `detect_secrets` into sys.modules."""
    pytest.skip("PR5 — TODO impl")


def test_cli_import_does_not_load_httpx() -> None:
    """Rubric §B criterion 13 — importing memeval.dreaming.cli does not pull `httpx` into sys.modules."""
    pytest.skip("PR5 — TODO impl")


# §C — Argparse top-level shape

def test_main_help_exits_zero() -> None:
    """Rubric §C criterion 14 — `main(["--help"])` exits 0."""
    pytest.skip("PR5 — TODO impl")


def test_main_no_subcommand_exits_one() -> None:
    """Rubric §C criterion 15 — `main([])` exits 1 with a stderr message naming the available subcommands."""
    pytest.skip("PR5 — TODO impl")


def test_main_bogus_subcommand_exits_one() -> None:
    """Rubric §C criterion 16 — `main(["bogus-subcommand"])` exits 1 (NOT 2)."""
    pytest.skip("PR5 — TODO impl")


def test_main_unknown_flag_exits_one() -> None:
    """Rubric §C criterion 17 — `main(["daydream", "--unknown-flag"])` exits 1 (NOT 2)."""
    pytest.skip("PR5 — TODO impl")


def test_cli_never_exits_with_code_two() -> None:
    """Rubric §C criterion 18 — cli.py never calls sys.exit(2) and `main` never returns 2 (CC reserves exit 2)."""
    pytest.skip("PR5 — TODO impl")


def test_main_registers_exactly_two_subcommands() -> None:
    """Rubric §C criterion 19 — argparse top-level registers exactly two subcommands: `daydream` and `dream`."""
    pytest.skip("PR5 — TODO impl")


def test_main_prog_name_is_daydream_cli() -> None:
    """Rubric §C criterion 20 — argparse parser's `prog=` is `daydream-cli`."""
    pytest.skip("PR5 — TODO impl")


# §D — `daydream` subcommand — argparse surface

def test_daydream_subcommand_help_exits_zero() -> None:
    """Rubric §D criterion 21 — `daydream-cli daydream --help` exits 0."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_subcommand_no_input_failopens_zero() -> None:
    """Rubric §D criterion 22 — `daydream-cli daydream` with no flags AND missing stdin exits 0 (fail-open)."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_session_optional_when_stdin_provides() -> None:
    """Rubric §D criterion 23 — `--session` is optional at the argparse layer (stdin JSON can provide `session_id`)."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_log_optional_when_stdin_provides() -> None:
    """Rubric §D criterion 24 — `--log` is optional at the argparse layer (stdin JSON can provide `transcript_path`)."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_log_is_pathlike_when_supplied() -> None:
    """Rubric §D criterion 25 — when `--log` is supplied, the parsed namespace `.log` attribute is a `pathlib.Path`."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_store_optional_pathlike() -> None:
    """Rubric §D criterion 26 — `--store` is optional and coerced to `pathlib.Path` when present."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_no_store_parses_clean() -> None:
    """Rubric §D criterion 27 — `daydream-cli daydream --session SID --log /tmp/x` without `--store` parses cleanly."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_reads_stdin_json_when_no_flags() -> None:
    """Rubric §D criterion 28 — with no flags and valid hook JSON on stdin, the CLI calls `engine.daydream` with kwargs from stdin."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_flags_override_stdin_json() -> None:
    """Rubric §D criterion 29 — explicit `--session`/`--log` flags override stdin JSON values when both are present."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_failopens_on_bad_stdin() -> None:
    """Rubric §D criterion 30 — empty/non-JSON/missing-keys stdin with no flags returns 0 and emits a WARNING naming `stdin`."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_records_hook_event_name() -> None:
    """Rubric §D criterion 31 — `hook_event_name` from stdin JSON is threaded into the `daydream.cli_resolved` event."""
    pytest.skip("PR5 — TODO impl")


# §E — `daydream` subcommand — engine wiring

def test_daydream_subcommand_invokes_engine() -> None:
    """Rubric §E criterion 32 — `daydream-cli daydream --session SID --log L` calls `engine.daydream` once with kwargs session_id=SID, log_path=Path(L)."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_threads_store_via_env_var_with_restore() -> None:
    """Rubric §E criterion 33 — `--store P` sets `os.environ['MEMORY_STORE']` during the engine call and restores prior state in try/finally."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_subcommand_uses_orchestrator() -> None:
    """Rubric §E criterion 34 — CLI passes the Orchestrator (not a raw `MemoryStore`) as the `store=` argument to `engine.daydream`."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_subcommand_exits_zero_on_success() -> None:
    """Rubric §E criterion 35 — on successful `engine.daydream` return, `main` returns 0."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_emits_cli_resolved_event() -> None:
    """Rubric §E criterion 36 — every `daydream` invocation that reaches `engine.daydream` emits one `daydream.cli_resolved` event with the five pinned fields."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_passes_session_id_raw_to_engine_subprocess() -> None:
    """Rubric §E criterion 37 — subprocess: a hostile session_id is passed raw to the engine; no file is written outside MEMORY_STORE's parent."""
    pytest.skip("PR5 — TODO impl")


# §F — `daydream` subcommand — fail-open contract (ADR-harness-006)

def test_daydream_subcommand_failopens_on_keyerror() -> None:
    """Rubric §F criterion 38 — `engine.daydream` raising `KeyError` (unset MEMORY_STORE) returns 0 and logs WARNING naming MEMORY_STORE."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_subcommand_failopens_on_filenotfounderror() -> None:
    """Rubric §F criterion 39 — `engine.daydream` raising `FileNotFoundError` returns 0 and logs WARNING naming the missing path."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_subcommand_failopens_on_valueerror() -> None:
    """Rubric §F criterion 40 — `engine.daydream` raising `ValueError` (MEMORY_STORE is a directory) returns 0 and logs WARNING."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_subcommand_failopens_on_unexpected_exception() -> None:
    """Rubric §F criterion 41 — `engine.daydream` raising any other `Exception` subclass returns 0 and logs WARNING naming the exception class."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_subcommand_does_not_swallow_keyboardinterrupt() -> None:
    """Rubric §F criterion 42 — `KeyboardInterrupt` raised by `engine.daydream` propagates out of `main`."""
    pytest.skip("PR5 — TODO impl")


def test_daydream_subcommand_does_not_swallow_systemexit() -> None:
    """Rubric §F criterion 43 — `SystemExit` raised by `engine.daydream` propagates (the outer except clause uses `Exception`, not bare)."""
    pytest.skip("PR5 — TODO impl")


def test_cli_failopen_catches_exception_not_bare() -> None:
    """Rubric §F criterion 44 — AST scan: fail-open except clauses catch `Exception` literally (never `BaseException`, never bare)."""
    pytest.skip("PR5 — TODO impl")


# §G — `dream --all` subcommand — argparse surface

def test_dream_subcommand_help_exits_zero() -> None:
    """Rubric §G criterion 45 — `daydream-cli dream --help` exits 0."""
    pytest.skip("PR5 — TODO impl")


def test_dream_subcommand_requires_all_flag_exits_one() -> None:
    """Rubric §G criterion 46 — `daydream-cli dream` without `--all` exits 1 (NOT 2)."""
    pytest.skip("PR5 — TODO impl")


def test_dream_subcommand_store_optional_pathlike() -> None:
    """Rubric §G criterion 47 — `daydream-cli dream --all` accepts an optional `--store P` flag with `Path` coercion."""
    pytest.skip("PR5 — TODO impl")


def test_dream_subcommand_all_is_required_boolean() -> None:
    """Rubric §G criterion 48 — `--all` is a required boolean flag (no value, no implicit default)."""
    pytest.skip("PR5 — TODO impl")


# §H — `dream --all` subcommand — fail-open carve-out (`worker.py` stub)

def test_dream_all_failopens_on_notimplementederror() -> None:
    """Rubric §H criterion 49 — `daydream-cli dream --all` returns 0 even though `DreamingWorker.run` raises `NotImplementedError`."""
    pytest.skip("PR5 — TODO impl")


def test_dream_all_emits_skipped_event() -> None:
    """Rubric §H criterion 50 — on `NotImplementedError`, the CLI emits the event `daydream.dream_all_skipped`."""
    pytest.skip("PR5 — TODO impl")


def test_dream_all_logs_notimplemented_visibly() -> None:
    """Rubric §H criterion 51 — on `NotImplementedError`, the CLI logs WARNING containing `night` or `consolidation`."""
    pytest.skip("PR5 — TODO impl")


def test_dream_all_failopens_and_emits_error_event() -> None:
    """Rubric §H criterion 52 — `DreamingWorker.run` raising any other exception returns 0, emits `daydream.dream_all_error`, and logs the class name."""
    pytest.skip("PR5 — TODO impl")


def test_worker_unchanged() -> None:
    """Rubric §H criterion 53 — worker.py is byte-identical to its pre-PR5 contents (PR5 does not touch night Dream)."""
    pytest.skip("PR5 — TODO impl")


def test_dream_all_threads_store_arg() -> None:
    """Rubric §H criterion 54 — `--store P` is threaded into the night-dream entrypoint via env-var with try/finally restore."""
    pytest.skip("PR5 — TODO impl")


def test_dream_all_does_not_swallow_keyboardinterrupt() -> None:
    """Rubric §H criterion 55 — `KeyboardInterrupt` raised by `DreamingWorker.run` propagates out of `dream --all`."""
    pytest.skip("PR5 — TODO impl")


# §L — Anti-slop (deterministic source-scan)

def test_no_todo_markers_in_pr5_modules() -> None:
    """Rubric §L criterion 82 — zero TODO/FIXME/XXX/HACK comments in eval/memeval/dreaming/cli.py."""
    pytest.skip("PR5 — TODO impl")


def test_no_print_calls_in_cli() -> None:
    """Rubric §L criterion 83 — AST scan: zero `print()` statements in cli.py."""
    pytest.skip("PR5 — TODO impl")


def test_main_does_not_call_sys_exit() -> None:
    """Rubric §L criterion 84 — AST scan: zero `sys.exit()` calls in `main` or any handler it dispatches to."""
    pytest.skip("PR5 — TODO impl")


def test_pragmas_are_justified_in_cli() -> None:
    """Rubric §L criterion 85 — every pragma/type-ignore/noqa in cli.py carries an inline `# REASON:` justification."""
    pytest.skip("PR5 — TODO impl")


def test_public_symbols_have_real_docstrings_pr5() -> None:
    """Rubric §L criterion 86 — every public function/class in cli.py (`main` at minimum) has a one-line docstring."""
    pytest.skip("PR5 — TODO impl")


def test_no_bare_except_in_cli() -> None:
    """Rubric §L criterion 87 — AST scan: every `except` in cli.py names an exception class (no bare `except:`)."""
    pytest.skip("PR5 — TODO impl")


def test_no_stub_function_bodies_in_cli() -> None:
    """Rubric §L criterion 88 — cli.py contains no stub function bodies (`pass`/lone `return None`) outside `__init__`."""
    pytest.skip("PR5 — TODO impl")


# §N — mypy `--strict` coverage extension

def test_cli_not_in_mypy_override_list() -> None:
    """Rubric §N criterion 95 — `memeval.dreaming.cli` is NOT in the pyproject mypy disallow_untyped_defs=false override list."""
    pytest.skip("PR5 — TODO impl")
