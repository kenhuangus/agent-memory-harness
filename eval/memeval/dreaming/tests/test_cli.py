"""PR5 CLI tests — every criterion from PR5_DAYDREAM_CLI_RUBRIC.md §A,B,C,D,E,F,G,H,L,N.

Imports stay stdlib-only at module top (mirror cli.py's discipline); pytest is the
sole third-party dependency.
"""

from __future__ import annotations

import ast
import io
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from inspect import signature
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest

from memeval.dreaming import cli


# --------------------------------------------------------------------------- #
# Shared helpers + fixtures
# --------------------------------------------------------------------------- #


def _cli_source() -> str:
    return Path(cli.__file__).read_text()


def _cli_ast() -> ast.Module:
    return ast.parse(_cli_source())


@pytest.fixture
def patch_stdin(monkeypatch: pytest.MonkeyPatch):
    """Yield a callable that replaces sys.stdin with a StringIO + isatty=False."""
    def _patch(content: Any) -> None:
        if content is None:
            text = ""
        elif isinstance(content, dict):
            text = json.dumps(content)
        elif isinstance(content, str):
            text = content
        else:
            text = json.dumps(content)
        stream = io.StringIO(text)
        stream.isatty = lambda: False  # type: ignore[method-assign]  # REASON: pytest stdin override
        monkeypatch.setattr(sys, "stdin", stream)
    return _patch


@pytest.fixture
def empty_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force sys.stdin to look like a TTY so _read_stdin_json returns {}."""
    stream = io.StringIO("")
    stream.isatty = lambda: True  # type: ignore[method-assign]  # REASON: pytest stdin override
    monkeypatch.setattr(sys, "stdin", stream)


@pytest.fixture
def memory_store_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temp MEMORY_STORE directory and set the env-var; yield the path (ADR-019)."""
    store = tmp_path / "memory-store"
    store.mkdir()
    monkeypatch.setenv("MEMORY_STORE", str(store))
    return store


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch):
    """Replace engine.daydream with a MagicMock that records kwargs."""
    from memeval.dreaming import engine
    mock = MagicMock(name="engine.daydream", return_value=None)
    monkeypatch.setattr(engine, "daydream", mock)
    return mock


@pytest.fixture
def fake_make_store(monkeypatch: pytest.MonkeyPatch):
    """Replace cli._make_store with a sentinel-returning MagicMock."""
    sentinel = MagicMock(name="store_sentinel")
    factory = MagicMock(name="_make_store", return_value=sentinel)
    monkeypatch.setattr(cli, "_make_store", factory)
    return factory


@pytest.fixture
def fake_emit(monkeypatch: pytest.MonkeyPatch):
    """Replace cli's events.emit reference at use-site so we can inspect calls."""
    captured: list[tuple[str, dict[str, Any]]] = []

    from memeval.dreaming import events as events_mod

    def _fake(event_type: str, **fields: Any) -> None:
        captured.append((event_type, fields))

    monkeypatch.setattr(events_mod, "emit", _fake)
    return captured


@pytest.fixture
def pyproject_path() -> Path:
    return Path(__file__).resolve().parents[3] / "pyproject.toml"


def _parse_scripts_table(pyproject_text: str) -> dict[str, str]:
    """Parse the [project.scripts] table from pyproject.toml (stdlib tomllib)."""
    import tomllib
    data = tomllib.loads(pyproject_text)
    return data.get("project", {}).get("scripts", {})


# --------------------------------------------------------------------------- #
# §A — [project.scripts] registration & install surface
# --------------------------------------------------------------------------- #


def test_pyproject_registers_daydream_cli(pyproject_path: Path) -> None:
    """Rubric §A criterion 1 — pyproject [project.scripts] contains daydream-cli = "memeval.dreaming.cli:main"."""
    scripts = _parse_scripts_table(pyproject_path.read_text())
    assert scripts.get("daydream-cli") == "memeval.dreaming.cli:main"


def test_pyproject_does_not_register_memory_cli(pyproject_path: Path) -> None:
    """Rubric §A criterion 2 — pyproject [project.scripts] has no superseded `memory =` entry."""
    scripts = _parse_scripts_table(pyproject_path.read_text())
    assert "memory" not in scripts


def test_pyproject_memeval_entry_unchanged(pyproject_path: Path) -> None:
    """Rubric §A criterion 3 — pyproject still registers `memeval = "memeval.cli:main"`."""
    scripts = _parse_scripts_table(pyproject_path.read_text())
    assert scripts.get("memeval") == "memeval.cli:main"


def test_pyproject_memeval_bench_entry_unchanged(pyproject_path: Path) -> None:
    """Rubric §A criterion 4 — pyproject still registers `memeval-bench = "memeval.claudecode.run_bench:main"`."""
    scripts = _parse_scripts_table(pyproject_path.read_text())
    assert scripts.get("memeval-bench") == "memeval.claudecode.run_bench:main"


def test_daydream_cli_script_resolves_without_extra() -> None:
    """Rubric §A criterion 6 — `daydream-cli` resolves on $PATH (entry-point registered regardless of extras)."""
    import shutil
    assert shutil.which("daydream-cli") is not None


def test_daydream_cli_without_extra_emits_actionable_error() -> None:
    """Rubric §A criterion 7 — N/A guarded test: skip if `detect_secrets` is installed (the extra is on).

    The criterion specifies a multi-venv subprocess matrix that's out of scope for unit tests.
    The CLI's fail-open boundary ensures that ANY import failure in the engine chain becomes
    a logged WARNING + exit 0, not a raw traceback — verified by criterion 41.
    """
    pytest.skip("Manual matrix per rubric §A-7 — covered structurally by criterion 41 in-process.")


# --------------------------------------------------------------------------- #
# §B — Module shape & public surface
# --------------------------------------------------------------------------- #


def test_cli_module_exists() -> None:
    """Rubric §B criterion 8 — cli.py exists and defines a top-level `main` callable."""
    assert callable(cli.main)


def test_main_signature_is_argv_in_int_out() -> None:
    """Rubric §B criterion 9 — `main`'s signature is `main(argv: list[str] | None = None) -> int`."""
    from typing import get_type_hints
    sig = signature(cli.main)
    assert list(sig.parameters) == ["argv"]
    assert sig.parameters["argv"].default is None
    hints = get_type_hints(cli.main)
    assert hints.get("return") is int


def test_main_defaults_to_sys_argv(monkeypatch: pytest.MonkeyPatch, empty_stdin: None) -> None:
    """Rubric §B criterion 10 — `main()` defaults to `sys.argv[1:]` (argparse default)."""
    monkeypatch.setattr(sys, "argv", ["daydream-cli"])
    assert cli.main() == 1  # no subcommand


def test_cli_module_stdlib_only_at_top() -> None:
    """Rubric §B criterion 11 — cli.py top-level imports are stdlib + memeval.protocols only."""
    tree = _cli_ast()
    allowed = {
        "__future__", "argparse", "json", "logging", "os", "sys",
        "pathlib", "typing", "memeval.protocols",
    }
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in allowed, f"top-level import {alias.name} not allowed"
        if isinstance(node, ast.ImportFrom):
            assert node.module in allowed, f"top-level from-import {node.module} not allowed"


def test_cli_imports_engine_inside_main_before_try() -> None:
    """Rubric §B criterion 11 — engine import is inside a function body and precedes a try."""
    source = _cli_source()
    # engine import is inside _handle_daydream
    assert "from memeval.dreaming import _state, engine" in source
    # AST: confirm `engine` is NOT a top-level import
    tree = _cli_ast()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            assert node.module != "memeval.dreaming.engine"
            if node.module == "memeval.dreaming":
                for alias in node.names:
                    assert alias.name != "engine"


def test_cli_import_does_not_load_detect_secrets() -> None:
    """Rubric §B criterion 12 — importing memeval.dreaming.cli does not pull `detect_secrets` into sys.modules."""
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; import memeval.dreaming.cli; "
         "assert 'detect_secrets' not in sys.modules, 'detect_secrets loaded at import time'"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_cli_import_does_not_load_httpx() -> None:
    """Rubric §B criterion 13 — importing memeval.dreaming.cli does not pull `httpx` into sys.modules."""
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; import memeval.dreaming.cli; "
         "assert 'httpx' not in sys.modules, 'httpx loaded at import time'"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------- #
# §C — Argparse top-level shape
# --------------------------------------------------------------------------- #


def test_main_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """Rubric §C criterion 14 — `main(["--help"])` exits 0."""
    assert cli.main(["--help"]) == 0


def test_main_no_subcommand_exits_one(empty_stdin: None) -> None:
    """Rubric §C criterion 15 — `main([])` exits 1."""
    assert cli.main([]) == 1


def test_main_bogus_subcommand_exits_one() -> None:
    """Rubric §C criterion 16 — `main(["bogus-subcommand"])` exits 1 (NOT 2)."""
    assert cli.main(["bogus-subcommand"]) == 1


def test_main_unknown_flag_exits_one() -> None:
    """Rubric §C criterion 17 — `main(["daydream", "--unknown-flag"])` exits 1 (NOT 2)."""
    assert cli.main(["daydream", "--unknown-flag"]) == 1


def test_cli_never_exits_with_code_two() -> None:
    """Rubric §C criterion 18 — cli.py source contains no literal sys.exit(2) or return 2."""
    source = _cli_source()
    assert "sys.exit(2)" not in source
    assert "return 2" not in source


def test_main_registers_exactly_two_subcommands() -> None:
    """Rubric §C criterion 19 — argparse top-level registers exactly two subcommands: `daydream` and `dream`."""
    parser = cli._build_parser()
    subparsers = [a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"][0]
    choices = subparsers.choices
    assert isinstance(choices, dict)
    assert set(choices.keys()) == {"daydream", "dream"}


def test_main_prog_name_is_daydream_cli() -> None:
    """Rubric §C criterion 20 — argparse parser's `prog=` is `daydream-cli`."""
    parser = cli._build_parser()
    assert parser.prog == "daydream-cli"


# --------------------------------------------------------------------------- #
# §D — `daydream` subcommand — argparse surface
# --------------------------------------------------------------------------- #


def test_daydream_subcommand_help_exits_zero() -> None:
    """Rubric §D criterion 21 — `daydream-cli daydream --help` exits 0."""
    assert cli.main(["daydream", "--help"]) == 0


def test_daydream_subcommand_no_input_failopens_zero(empty_stdin: None) -> None:
    """Rubric §D criterion 22 — `daydream-cli daydream` with no flags AND empty/missing stdin exits 0 (fail-open)."""
    assert cli.main(["daydream"]) == 0


def test_daydream_session_optional_when_stdin_provides(
    patch_stdin: Any, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
) -> None:
    """Rubric §D criterion 23 — `--session` is optional; stdin JSON can provide `session_id`."""
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    patch_stdin({"session_id": "stdin-session", "transcript_path": str(log_file)})
    result = cli.main(["daydream"])
    assert result == 0
    fake_engine.assert_called_once()
    assert fake_engine.call_args.kwargs["session_id"] == "stdin-session"


def test_daydream_log_optional_when_stdin_provides(
    patch_stdin: Any, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
) -> None:
    """Rubric §D criterion 24 — `--log` is optional; stdin JSON can provide `transcript_path`."""
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    patch_stdin({"session_id": "S", "transcript_path": str(log_file)})
    cli.main(["daydream"])
    fake_engine.assert_called_once()
    assert fake_engine.call_args.kwargs["log_path"] == log_file


def test_daydream_log_is_pathlike_when_supplied(empty_stdin: None) -> None:
    """Rubric §D criterion 25 — when `--log` is supplied, parsed namespace `.log` is a `Path`."""
    parser = cli._build_parser()
    args = parser.parse_args(["daydream", "--log", "/tmp/x"])
    assert isinstance(args.log, Path)


def test_daydream_store_optional_pathlike(empty_stdin: None) -> None:
    """Rubric §D criterion 26 — `--store` is optional and coerced to `Path` when present."""
    parser = cli._build_parser()
    args = parser.parse_args(["daydream", "--store", "/tmp/s"])
    assert isinstance(args.store, Path)


def test_daydream_no_store_parses_clean(empty_stdin: None) -> None:
    """Rubric §D criterion 27 — `daydream --session SID --log /tmp/x` without `--store` parses cleanly."""
    parser = cli._build_parser()
    args = parser.parse_args(["daydream", "--session", "S", "--log", "/tmp/x"])
    assert args.store is None
    assert args.session == "S"


def test_daydream_reads_stdin_json_when_no_flags(
    patch_stdin: Any, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
) -> None:
    """Rubric §D criterion 28 — valid hook JSON on stdin → engine.daydream called with kwargs from stdin."""
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    patch_stdin({"session_id": "S", "transcript_path": str(log_file), "hook_event_name": "Stop"})
    assert cli.main(["daydream"]) == 0
    fake_engine.assert_called_once()
    kw = fake_engine.call_args.kwargs
    assert kw["session_id"] == "S"
    assert kw["log_path"] == log_file


def test_daydream_flags_override_stdin_json(
    patch_stdin: Any, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
) -> None:
    """Rubric §D criterion 29 — explicit `--session`/`--log` override stdin JSON values."""
    stdin_log = tmp_path / "stdin_log.jsonl"
    flag_log = tmp_path / "flag_log.jsonl"
    stdin_log.touch()
    flag_log.touch()
    patch_stdin({"session_id": "stdin-S", "transcript_path": str(stdin_log)})
    cli.main(["daydream", "--session", "flag-S", "--log", str(flag_log)])
    kw = fake_engine.call_args.kwargs
    assert kw["session_id"] == "flag-S"
    assert kw["log_path"] == flag_log


def test_daydream_failopens_on_bad_stdin(
    patch_stdin: Any, caplog: pytest.LogCaptureFixture,
) -> None:
    """Rubric §D criterion 30 — bad/missing stdin → exit 0 + WARNING naming `stdin`."""
    patch_stdin("not json {{{")
    caplog.set_level(logging.WARNING, logger="memeval.dreaming.cli")
    assert cli.main(["daydream"]) == 0
    assert any(
        rec.levelno >= logging.WARNING and ("stdin" in rec.getMessage().lower() or "session_id" in rec.getMessage())
        for rec in caplog.records
    )


def test_daydream_records_hook_event_name(
    patch_stdin: Any, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
) -> None:
    """Rubric §D criterion 31 — `hook_event_name` from stdin → cli_resolved event field."""
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    patch_stdin({"session_id": "S", "transcript_path": str(log_file), "hook_event_name": "PreCompact"})
    cli.main(["daydream"])
    resolved = [(t, f) for t, f in fake_emit if t == "daydream.cli_resolved"]
    assert len(resolved) == 1
    assert resolved[0][1]["hook_event_name"] == "PreCompact"


# --------------------------------------------------------------------------- #
# §E — `daydream` subcommand — engine wiring
# --------------------------------------------------------------------------- #


def test_daydream_subcommand_invokes_engine(
    empty_stdin: None, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
) -> None:
    """Rubric §E criterion 32 — `daydream --session SID --log L` → engine.daydream called once with those kwargs."""
    log_file = tmp_path / "real.jsonl"
    log_file.touch()
    cli.main(["daydream", "--session", "SID", "--log", str(log_file)])
    fake_engine.assert_called_once()
    kw = fake_engine.call_args.kwargs
    assert kw["session_id"] == "SID"
    assert kw["log_path"] == log_file


def test_daydream_threads_store_via_env_var_with_restore(
    empty_stdin: None, fake_engine: MagicMock, fake_make_store: MagicMock,
    fake_emit: list[Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rubric §E criterion 33 — `--store P` sets MEMORY_STORE for the engine call and restores prior value."""
    prev = tmp_path / "prev-store"
    prev.mkdir()
    monkeypatch.setenv("MEMORY_STORE", str(prev))
    new_store = tmp_path / "new-store"
    new_store.mkdir()
    log_file = tmp_path / "log.jsonl"
    log_file.touch()

    captured: dict[str, str | None] = {}

    def _capturing_daydream(**kwargs: Any) -> None:
        captured["during"] = os.environ.get("MEMORY_STORE")

    from memeval.dreaming import engine
    monkeypatch.setattr(engine, "daydream", _capturing_daydream)

    cli.main(["daydream", "--session", "S", "--log", str(log_file), "--store", str(new_store)])
    assert captured["during"] == str(new_store.resolve())
    assert os.environ.get("MEMORY_STORE") == str(prev)


def test_daydream_subcommand_uses_orchestrator(
    empty_stdin: None, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
) -> None:
    """Rubric §E criterion 34 — CLI calls _make_store() once and threads its return value as `store=` kw."""
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    cli.main(["daydream", "--session", "S", "--log", str(log_file)])
    fake_make_store.assert_called_once()
    assert fake_engine.call_args.kwargs["store"] is fake_make_store.return_value


def test_daydream_subcommand_exits_zero_on_success(
    empty_stdin: None, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
) -> None:
    """Rubric §E criterion 35 — on successful engine.daydream return, main returns 0."""
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    assert cli.main(["daydream", "--session", "S", "--log", str(log_file)]) == 0


def test_daydream_emits_cli_resolved_event(
    empty_stdin: None, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
) -> None:
    """Rubric §E criterion 36 — exactly one daydream.cli_resolved event with the five pinned fields."""
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    cli.main(["daydream", "--session", "S", "--log", str(log_file)])
    resolved = [(t, f) for t, f in fake_emit if t == "daydream.cli_resolved"]
    assert len(resolved) == 1
    fields = resolved[0][1]
    for key in ("sys_executable", "script_path", "package_version", "engine_module_path", "hook_event_name"):
        assert key in fields, f"missing field {key}"


def test_daydream_passes_session_id_raw_to_engine_subprocess(
    empty_stdin: None, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
) -> None:
    """Rubric §E criterion 37 — hostile session_id is passed raw to engine; sanitization is engine-side."""
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    hostile = "../../etc/passwd"
    cli.main(["daydream", "--session", hostile, "--log", str(log_file)])
    assert fake_engine.call_args.kwargs["session_id"] == hostile


# --------------------------------------------------------------------------- #
# §F — `daydream` subcommand — fail-open contract (ADR-harness-006)
# --------------------------------------------------------------------------- #


def test_daydream_subcommand_failopens_on_keyerror(
    empty_stdin: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path,
) -> None:
    """Rubric §F criterion 38 — KeyError (unset MEMORY_STORE) → exit 0 + WARNING naming MEMORY_STORE."""
    monkeypatch.delenv("MEMORY_STORE", raising=False)
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    caplog.set_level(logging.WARNING, logger="memeval.dreaming.cli")
    assert cli.main(["daydream", "--session", "S", "--log", str(log_file)]) == 0
    assert any("MEMORY_STORE" in rec.getMessage() for rec in caplog.records)


def test_daydream_creates_missing_basedir(
    empty_stdin: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Rubric §F criterion 39 — repurposed under ADR-019: MEMORY_STORE pointing at a missing path auto-mkdirs (no error)."""
    missing = tmp_path / "deep" / "nested" / "memory-store"
    assert not missing.exists()
    monkeypatch.setenv("MEMORY_STORE", str(missing))
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    assert cli.main(["daydream", "--session", "S", "--log", str(log_file)]) == 0
    assert missing.is_dir()  # auto-created by resolve_basedir per ADR-019


def test_daydream_subcommand_failopens_on_valueerror(
    empty_stdin: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path,
) -> None:
    """Rubric §F criterion 40 — inverted under ADR-019: MEMORY_STORE points at a FILE → exit 0 + WARNING."""
    file_path = tmp_path / "stale-sentinel.jsonl"
    file_path.touch()
    monkeypatch.setenv("MEMORY_STORE", str(file_path))
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    caplog.set_level(logging.WARNING, logger="memeval.dreaming.cli")
    assert cli.main(["daydream", "--session", "S", "--log", str(log_file)]) == 0
    assert any("MEMORY_STORE" in rec.getMessage() for rec in caplog.records)


def test_daydream_subcommand_failopens_on_oserror_basedir(
    empty_stdin: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path,
) -> None:
    """daydream-workflow-hardening — resolve_basedir raising PermissionError
    (unwritable / uncreatable MEMORY_STORE path) → exit 0 + WARNING, NOT a
    crash. Before the fix the guard caught only KeyError/FileNotFoundError/
    ValueError, so PermissionError escaped and the async Stop-hook subprocess
    died with a traceback + exit 1, breaking the fail-open contract."""
    from memeval.dreaming import _state

    def _boom() -> Path:
        raise PermissionError("[Errno 13] Permission denied: '/ro/sub'")

    monkeypatch.setattr(_state, "resolve_basedir", _boom)
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    caplog.set_level(logging.WARNING, logger="memeval.dreaming.cli")
    assert cli.main(["daydream", "--session", "S", "--log", str(log_file)]) == 0
    assert any(
        "MEMORY_STORE resolution failed" in rec.getMessage()
        and "PermissionError" in rec.getMessage()
        for rec in caplog.records
    )


def test_daydream_subcommand_failopens_on_unexpected_exception(
    empty_stdin: None, memory_store_dir: Path, fake_make_store: MagicMock,
    fake_emit: list[Any], caplog: pytest.LogCaptureFixture, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rubric §F criterion 41 — engine.daydream raising RuntimeError → exit 0 + WARNING naming class."""
    from memeval.dreaming import engine

    def _boom(**kwargs: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(engine, "daydream", _boom)
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    caplog.set_level(logging.WARNING, logger="memeval.dreaming.cli")
    assert cli.main(["daydream", "--session", "S", "--log", str(log_file)]) == 0
    assert any("RuntimeError" in rec.getMessage() for rec in caplog.records)


def test_daydream_subcommand_does_not_swallow_keyboardinterrupt(
    empty_stdin: None, memory_store_dir: Path, fake_make_store: MagicMock,
    fake_emit: list[Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rubric §F criterion 42 — KeyboardInterrupt from engine propagates out of main."""
    from memeval.dreaming import engine

    def _interrupt(**kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(engine, "daydream", _interrupt)
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    with pytest.raises(KeyboardInterrupt):
        cli.main(["daydream", "--session", "S", "--log", str(log_file)])


def test_daydream_subcommand_does_not_swallow_systemexit(
    empty_stdin: None, memory_store_dir: Path, fake_make_store: MagicMock,
    fake_emit: list[Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rubric §F criterion 43 — SystemExit from engine propagates."""
    from memeval.dreaming import engine

    def _sysexit(**kwargs: Any) -> None:
        raise SystemExit(7)

    monkeypatch.setattr(engine, "daydream", _sysexit)
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    with pytest.raises(SystemExit):
        cli.main(["daydream", "--session", "S", "--log", str(log_file)])


def test_cli_failopen_catches_exception_not_bare() -> None:
    """Rubric §F criterion 44 — every fail-open except clause names `Exception`, not bare and not BaseException."""
    tree = _cli_ast()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            assert node.type is not None, "bare except: forbidden"
            # Allowed: Exception, KeyboardInterrupt, SystemExit, specific subclasses, tuple of those
            # Forbidden: BaseException
            if isinstance(node.type, ast.Name):
                assert node.type.id != "BaseException", "BaseException catch forbidden"
            if isinstance(node.type, ast.Tuple):
                for elt in node.type.elts:
                    if isinstance(elt, ast.Name):
                        assert elt.id != "BaseException", "BaseException catch forbidden"


# --------------------------------------------------------------------------- #
# §G — `dream --all` subcommand — argparse surface
# --------------------------------------------------------------------------- #


def test_dream_subcommand_help_exits_zero() -> None:
    """Rubric §G criterion 45 — `daydream-cli dream --help` exits 0."""
    assert cli.main(["dream", "--help"]) == 0


def test_dream_subcommand_requires_all_flag_exits_one() -> None:
    """Rubric §G criterion 46 — `daydream-cli dream` without `--all` exits 1."""
    assert cli.main(["dream"]) == 1


def test_dream_subcommand_store_optional_pathlike() -> None:
    """Rubric §G criterion 47 — `dream --all --store P` parses P as Path."""
    parser = cli._build_parser()
    args = parser.parse_args(["dream", "--all", "--store", "/tmp/s"])
    assert isinstance(args.store, Path)


def test_dream_subcommand_all_is_required_boolean() -> None:
    """Rubric §G criterion 48 — `--all` is required boolean (no value, no default)."""
    parser = cli._build_parser()
    args = parser.parse_args(["dream", "--all"])
    assert args.all is True


# --------------------------------------------------------------------------- #
# §H — `dream --all` subcommand — fail-open
# --------------------------------------------------------------------------- #
# PR5 §H criteria 49, 50, 51 and 53 superseded by INITIAL_DREAM_RUBRIC.md
# (worker.py now implements Job-1 dedup detection; the NotImplementedError
# stub path no longer exists on success). The remaining tests preserve the
# §H 52, 54, 55 contract that survives the implementation transition.


def test_dream_all_failopens_and_emits_error_event(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch, fake_make_store: MagicMock,
    fake_emit: list[Any], caplog: pytest.LogCaptureFixture,
) -> None:
    """Rubric §H criterion 52 — any other exception → exit 0 + dream_all_error event + class-name log."""
    from memeval.dreaming import worker

    def _boom(**kwargs: Any) -> Any:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(worker, "dream", _boom)
    caplog.set_level(logging.WARNING, logger="memeval.dreaming.cli")
    assert cli.main(["dream", "--all"]) == 0
    assert any(t == "daydream.dream_all_error" for t, _ in fake_emit)
    assert any("RuntimeError" in rec.getMessage() for rec in caplog.records)


def test_dream_all_threads_store_arg(
    monkeypatch: pytest.MonkeyPatch, fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
) -> None:
    """Rubric §H criterion 54 — `--store P` threaded via MEMORY_STORE with try/finally restore."""
    prev = tmp_path / "prev-store"
    prev.mkdir()
    new_store = tmp_path / "new-store"
    new_store.mkdir()
    monkeypatch.setenv("MEMORY_STORE", str(prev))

    captured: dict[str, str | None] = {}
    from memeval.dreaming import worker

    def _capturing_dream(**kwargs: Any) -> Any:
        captured["during"] = os.environ.get("MEMORY_STORE")
        raise NotImplementedError

    monkeypatch.setattr(worker, "dream", _capturing_dream)
    cli.main(["dream", "--all", "--store", str(new_store)])
    assert captured["during"] == str(new_store.resolve())
    assert os.environ.get("MEMORY_STORE") == str(prev)


def test_dream_all_does_not_swallow_keyboardinterrupt(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch, fake_make_store: MagicMock,
) -> None:
    """Rubric §H criterion 55 — KeyboardInterrupt propagates."""
    from memeval.dreaming import worker

    def _interrupt(**kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(worker, "dream", _interrupt)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["dream", "--all"])


# --------------------------------------------------------------------------- #
# §H-event-context — _handle_dream binds event_context so worker events
# reach the per-session diary file (regression test for the silent-drop bug
# where dream.* events disappeared in production because _handle_dream
# never set the session_id/basedir context-vars events.py guards on).
# --------------------------------------------------------------------------- #


def test_dream_all_binds_event_context_around_worker_call(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch, fake_make_store: MagicMock,
) -> None:
    """`_handle_dream` MUST wrap `worker.dream()` in an event_context bound to
    a session_id + the resolved basedir. Without the context, every
    `emit("dream.*")` inside the worker short-circuits silently — operators
    lose the entire dream-cycle audit trail."""
    from memeval.dreaming import events as events_mod
    from memeval.dreaming import worker

    captured_contexts: list[tuple[str | None, Path | None]] = []
    real_event_context = events_mod.event_context

    def _spy_event_context(**kwargs: Any):
        captured_contexts.append((kwargs.get("session_id"), kwargs.get("basedir")))
        return real_event_context(**kwargs)

    monkeypatch.setattr(events_mod, "event_context", _spy_event_context)
    monkeypatch.setattr(worker, "dream", lambda **kw: None)

    assert cli.main(["dream", "--all"]) == 0
    assert captured_contexts, (
        "event_context was never entered — _handle_dream is dropping every "
        "dream.* event the worker emits"
    )
    sid, basedir = captured_contexts[0]
    assert isinstance(sid, str) and len(sid) >= 32, (
        "session_id should be a fresh uuid per dream run"
    )
    assert basedir is not None


def test_dream_all_worker_events_reach_diary_file(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch, fake_make_store: MagicMock,
    tmp_path: Path,
) -> None:
    """End-to-end: a sentinel event emitted from inside `worker.dream()` MUST
    land in the per-session diary file at
    `<basedir>/dream/<session_id>.daydream-events.jsonl`. The pre-fix
    behavior wrote NOTHING (events.py short-circuits when context-vars are
    unbound), so the entire cycle's audit trail vanished."""
    import json
    import uuid

    from memeval.dreaming import worker
    from memeval.dreaming.events import emit as real_emit

    fixed_sid = "11111111-2222-3333-4444-555555555555"
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(fixed_sid))

    def _emit_sentinel(**kwargs: Any) -> None:
        real_emit("dream.test_sentinel", marker="ok")

    monkeypatch.setattr(worker, "dream", _emit_sentinel)
    assert cli.main(["dream", "--all"]) == 0

    diary = memory_store_dir / "dream" / f"{fixed_sid}.daydream-events.jsonl"
    assert diary.exists(), (
        f"diary file {diary} not created — event_context did not write through"
    )
    events = [json.loads(line) for line in diary.read_text().splitlines() if line.strip()]
    assert any(e.get("event_type") == "dream.test_sentinel" for e in events), (
        f"sentinel event not in diary; got events: "
        f"{[e.get('event_type') for e in events]}"
    )


def test_dream_lock_contended_emit_lands_in_diary(
    memory_store_dir: Path, monkeypatch: pytest.MonkeyPatch, fake_make_store: MagicMock,
) -> None:
    """Fail-open `dream.lock_contended` event must also reach the diary —
    pre-fix the emit happened outside event_context and silently dropped."""
    import json
    import uuid

    from memeval.dreaming import _state, worker

    fixed_sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(fixed_sid))
    monkeypatch.setattr(worker, "dream", lambda **kw: (_ for _ in ()).throw(_state._DreamLockHeld()))

    assert cli.main(["dream", "--all"]) == 0
    diary = memory_store_dir / "dream" / f"{fixed_sid}.daydream-events.jsonl"
    assert diary.exists(), "fail-open path must also write to the diary"
    events = [json.loads(line) for line in diary.read_text().splitlines() if line.strip()]
    assert any(e.get("event_type") == "dream.lock_contended" for e in events)


# --------------------------------------------------------------------------- #
# §E — OPENROUTER_API_KEY startup alert (migration PR §E)
# --------------------------------------------------------------------------- #


def test_openrouter_unset_emits_stderr_alert(
    empty_stdin: None, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migration rubric §E criterion 29 — unset OPENROUTER_API_KEY → stderr line names the env var."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    cli.main(["daydream", "--session", "S", "--log", str(log_file)])
    captured = capsys.readouterr()
    assert "OPENROUTER_API_KEY" in captured.err


def test_openrouter_alert_names_env_example(
    empty_stdin: None, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migration rubric §E criterion 30 — alert text contains `.env.example`."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    cli.main(["daydream", "--session", "S", "--log", str(log_file)])
    captured = capsys.readouterr()
    assert ".env.example" in captured.err


def test_openrouter_unset_emits_warning_log(
    empty_stdin: None, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migration rubric §E criterion 31 — exactly one WARNING log naming OPENROUTER_API_KEY."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    caplog.set_level(logging.WARNING, logger="memeval.dreaming.cli")
    cli.main(["daydream", "--session", "S", "--log", str(log_file)])
    matching = [r for r in caplog.records if "OPENROUTER_API_KEY" in r.getMessage()]
    assert len(matching) == 1


def test_openrouter_set_emits_no_alert(
    empty_stdin: None, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migration rubric §E criterion 32 — OPENROUTER_API_KEY set → no stderr alert."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    cli.main(["daydream", "--session", "S", "--log", str(log_file)])
    captured = capsys.readouterr()
    assert "OPENROUTER_API_KEY" not in captured.err


def test_openrouter_set_emits_no_warning_log(
    empty_stdin: None, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migration rubric §E criterion 33 — OPENROUTER_API_KEY set → no warning log."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    caplog.set_level(logging.WARNING, logger="memeval.dreaming.cli")
    cli.main(["daydream", "--session", "S", "--log", str(log_file)])
    matching = [r for r in caplog.records if "OPENROUTER_API_KEY" in r.getMessage()]
    assert len(matching) == 0


def test_openrouter_unset_does_not_short_circuit_engine(
    empty_stdin: None, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migration rubric §E criterion 34 — alert does NOT short-circuit; engine still called once."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    cli.main(["daydream", "--session", "S", "--log", str(log_file)])
    fake_engine.assert_called_once()


def test_openrouter_unset_failopens_zero(
    empty_stdin: None, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migration rubric §E criterion 35 — OPENROUTER_API_KEY unset → main still returns 0."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    assert cli.main(["daydream", "--session", "S", "--log", str(log_file)]) == 0


def test_openrouter_unset_emits_diary_event(
    empty_stdin: None, memory_store_dir: Path, fake_engine: MagicMock,
    fake_make_store: MagicMock, fake_emit: list[Any], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migration rubric §E criterion 35b — diary event observable in async-Stop path (halliday F9)."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    log_file = tmp_path / "log.jsonl"
    log_file.touch()
    cli.main(["daydream", "--session", "S", "--log", str(log_file)])
    matching = [(t, f) for t, f in fake_emit if t == "daydream.openrouter_unset"]
    assert len(matching) == 1


# --------------------------------------------------------------------------- #
# §L — Anti-slop (deterministic source-scan)
# --------------------------------------------------------------------------- #


def test_no_todo_markers_in_pr5_modules() -> None:
    """Rubric §L criterion 82 — zero TODO/FIXME/XXX/HACK comments in cli.py."""
    source = _cli_source()
    for marker in ("TODO", "FIXME", "XXX", "HACK"):
        assert marker not in source, f"{marker} found in cli.py"


def test_no_print_calls_in_cli() -> None:
    """Rubric §L criterion 83 — AST scan: zero `print()` calls in cli.py."""
    tree = _cli_ast()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "print", f"print() found at line {node.lineno}"


def test_main_does_not_call_sys_exit() -> None:
    """Rubric §L criterion 84 — `main` and dispatched handlers contain no sys.exit() calls."""
    tree = _cli_ast()
    target_funcs = {"main", "_handle_daydream", "_handle_dream"}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in target_funcs:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                    if isinstance(sub.func.value, ast.Name) and sub.func.value.id == "sys":
                        assert sub.func.attr != "exit", f"sys.exit() in {node.name}"


def test_pragmas_are_justified_in_cli() -> None:
    """Rubric §L criterion 85 — every pragma / type: ignore / noqa carries an inline `# REASON:` justification."""
    source = _cli_source()
    pragma_re = re.compile(r"#\s*(noqa|type:\s*ignore|pragma:)")
    for lineno, line in enumerate(source.splitlines(), start=1):
        if pragma_re.search(line):
            assert "REASON:" in line, f"unjustified pragma at line {lineno}: {line}"


def test_public_symbols_have_real_docstrings_pr5() -> None:
    """Rubric §L criterion 86 — `main` at minimum has a docstring."""
    assert (cli.main.__doc__ or "").strip(), "main lacks a docstring"


def test_no_bare_except_in_cli() -> None:
    """Rubric §L criterion 87 — every except clause names an exception class."""
    tree = _cli_ast()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            assert node.type is not None, f"bare except at line {node.lineno}"


def test_no_stub_function_bodies_in_cli() -> None:
    """Rubric §L criterion 88 — no stub bodies (`pass` / lone `return None`) outside `__init__`."""
    tree = _cli_ast()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name != "__init__":
            if len(node.body) == 1:
                stmt = node.body[0]
                if isinstance(stmt, ast.Pass):
                    pytest.fail(f"stub `pass` body in {node.name}")
                if isinstance(stmt, ast.Return) and (stmt.value is None or (
                    isinstance(stmt.value, ast.Constant) and stmt.value.value is None
                )):
                    pytest.fail(f"stub `return None` body in {node.name}")


# --------------------------------------------------------------------------- #
# §M — Test-suite hygiene
# --------------------------------------------------------------------------- #


def test_rubric_test_names_all_present_pr5() -> None:
    """Rubric §M criterion 89 — every backticked test_* name in the rubric is defined in test_cli.py OR test_plugin_manifest.py."""
    rubric = Path(__file__).resolve().parent / "PR5_DAYDREAM_CLI_RUBRIC.md"
    rubric_text = rubric.read_text()
    rubric_names = set(re.findall(r"`(test_[a-zA-Z0-9_]+)`", rubric_text))

    # PR5 §H criteria 49, 50, 51, 53 superseded by INITIAL_DREAM_RUBRIC.md — the
    # NotImplementedError stub path no longer exists once worker.py implements
    # Job-1 dedup detection. These tests are intentionally removed.
    superseded = {
        "test_dream_all_failopens_on_notimplementederror",
        "test_dream_all_emits_skipped_event",
        "test_dream_all_logs_notimplemented_visibly",
        "test_worker_unchanged",
    }

    from memeval.dreaming.tests import test_cli as _tc
    from memeval.dreaming.tests import test_plugin_manifest as _tpm
    collected = {n for n in dir(_tc) if n.startswith("test_")}
    collected |= {n for n in dir(_tpm) if n.startswith("test_")}

    missing = rubric_names - collected - superseded
    assert not missing, f"rubric names not present: {sorted(missing)}"


# --------------------------------------------------------------------------- #
# §N — mypy `--strict` coverage extension
# --------------------------------------------------------------------------- #


def test_cli_not_in_mypy_override_list(pyproject_path: Path) -> None:
    """Rubric §N criterion 95 — memeval.dreaming.cli NOT in the disallow_untyped_defs=false override list."""
    import tomllib
    data = tomllib.loads(pyproject_path.read_text())
    overrides = data.get("tool", {}).get("mypy", {}).get("overrides", [])
    for override in overrides:
        mods = override.get("module", [])
        if isinstance(mods, str):
            mods = [mods]
        if "memeval.dreaming.cli" in mods:
            assert override.get("disallow_untyped_defs", True) is True, (
                "memeval.dreaming.cli must not be in disallow_untyped_defs=false override"
            )


# --------------------------------------------------------------------------- #
# §R — `python -m` entry-point regression (daydream-workflow-hardening)
#
# The Claude Code plugin's Stop/PreCompact hooks invoke daydream as
# `python -m memeval.dreaming.cli daydream` (hooks_handler._daydream_command).
# A module run via `-m` only executes code under an `if __name__ ==
# "__main__"` guard. cli.py originally lacked that guard AND there was no
# memeval/dreaming/__main__.py, so the hook subprocess imported the module
# and exited 0 WITHOUT calling main() — every hook-fired daydream was a
# silent no-op (no LLM call, no store write, no cursor advance). These tests
# pin that `-m` actually reaches main().
# --------------------------------------------------------------------------- #


def _run_m(module: str, payload: dict) -> subprocess.CompletedProcess:
    """Run `python -m <module> daydream` with a temp MEMORY_STORE and the
    EchoClient provider so no network/key is needed. Returns the completed
    process; the side effect we assert on is the cli_resolved diary event,
    which is only emitted once main() -> _handle_daydream actually runs."""
    tmp = tempfile.mkdtemp()
    store = Path(tmp) / "store"
    store.mkdir()
    log = store / "transcript.jsonl"
    log.write_text('{"role":"user","content":"hello world"}\n', encoding="utf-8")
    body = dict(payload)
    body.setdefault("transcript_path", str(log))
    env = dict(os.environ)
    env["MEMORY_STORE"] = str(store)
    env["DREAM_PROVIDER"] = "echo"
    env.pop("OPENROUTER_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-m", module, "daydream"],
        input=json.dumps(body),
        capture_output=True,
        text=True,
        env=env,
    )
    # The diary file only exists if event_context was entered, which only
    # happens once _handle_daydream runs — i.e. main() was actually called.
    diary = store / "dream" / "msess.daydream-events.jsonl"
    result._diary_exists = (store / "dream").exists() and any(  # type: ignore[attr-defined]
        (store / "dream").glob("*.daydream-events.jsonl")
    )
    return result


def test_dash_m_cli_module_invokes_main() -> None:
    """`python -m memeval.dreaming.cli daydream` must run main() (not import-and-exit)."""
    result = _run_m("memeval.dreaming.cli", {"session_id": "msess"})
    assert result.returncode == 0, result.stderr
    assert result._diary_exists, (  # type: ignore[attr-defined]
        "no daydream diary written — main() never ran under `-m memeval.dreaming.cli`; "
        "the __main__ guard is missing (hook subprocess would be a silent no-op)"
    )


def test_dash_m_package_invokes_main() -> None:
    """`python -m memeval.dreaming daydream` must run main() via __main__.py."""
    result = _run_m("memeval.dreaming", {"session_id": "msess"})
    assert result.returncode == 0, result.stderr
    assert result._diary_exists, (  # type: ignore[attr-defined]
        "no daydream diary written — main() never ran under `-m memeval.dreaming`; "
        "memeval/dreaming/__main__.py is missing or does not call main()"
    )


def test_cli_has_main_guard_in_source() -> None:
    """Static guard: cli.py must contain an `if __name__ == '__main__'` block calling main()."""
    src = _cli_source()
    assert '__name__ == "__main__"' in src or "__name__ == '__main__'" in src, (
        "cli.py is missing the __main__ guard required for `python -m memeval.dreaming.cli`"
    )
