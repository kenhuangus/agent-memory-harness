"""daydream-cli console script — entry point per ADR-dreaming-016.

The Claude Code plugin's ``Stop`` (async) and ``PreCompact`` (sync) hooks
shell out to ``daydream-cli daydream``; the CLI reads the hook's stdin
JSON payload (``session_id``, ``transcript_path``, ``hook_event_name``)
and forwards to :func:`memeval.dreaming.engine.daydream`. Explicit
``--session`` / ``--log`` flags override stdin keys for manual /
test invocation.

Argparse-error exit code is **1, not 2** per ADR-dreaming-018: the CC
plugin-hooks contract reserves exit 2 as "block this hook's action."
:class:`_NonExitingParser` raises :class:`argparse.ArgumentError` and
:func:`main` returns 1.

``--store`` threads through :envvar:`MEMORY_STORE` per ADR-dreaming-019
(`MEMORY_STORE` is a directory; supersedes ADR-015 §1), with try/finally
restore of the prior value (ADR-dreaming-017 §X).

Store factory: :func:`_make_store` returns the :class:`RouterStore` built
by :func:`cookbook_memory.core.contract.build_store` — the same single
assembly seam the plugin's ``_Engine`` uses (ADR-harness-011). Daydream
writes are routed + deduped across vector/markdown/graph backends per
the auto-selected profile, so daydream-extracted memories get the same
treatment as ``memory-cli remember`` writes (no markdown-only bypass).

``dream --all`` calls :func:`memeval.dreaming.worker.dream`; the v1
``worker.DreamingWorker.run`` is a stub that raises
``NotImplementedError`` — the CLI catches it, emits a
``daydream.dream_all_skipped`` event, and exits 0 (fail-open).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, NoReturn, cast

from memeval.protocols import MemoryStore

log = logging.getLogger(__name__)


class _NonExitingParser(argparse.ArgumentParser):
    """ArgumentParser that raises ArgumentError on parse failure instead of exiting 2 (ADR-018)."""

    def error(self, message: str) -> NoReturn:
        raise argparse.ArgumentError(None, message)


def _build_parser() -> argparse.ArgumentParser:
    """Build the top-level daydream-cli argparse parser with daydream and dream subcommands."""
    parser = _NonExitingParser(prog="daydream-cli")
    subparsers = parser.add_subparsers(dest="subcommand", metavar="{daydream,dream}")

    daydream_p = subparsers.add_parser("daydream")
    daydream_p.add_argument("--session", type=str, default=None)
    daydream_p.add_argument("--log", type=Path, default=None)
    daydream_p.add_argument("--store", type=Path, default=None)

    dream_p = subparsers.add_parser("dream")
    dream_p.add_argument("--all", action="store_true", required=True)
    dream_p.add_argument("--store", type=Path, default=None)

    return parser


def _read_stdin_json() -> dict[str, Any]:
    """Return the hook stdin JSON payload, or {} on empty/invalid stdin (fail-open)."""
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _make_store(basedir: Path) -> MemoryStore:
    """Return the v1 MemoryStore — :class:`RouterStore` via :func:`cookbook_memory.core.contract.build_store`.

    Mirrors the plugin's single assembly seam (ADR-harness-011) so daydream
    writes go through the same Router policy that the plugin's ``_Engine``
    uses: dedup + write-routing across all backends per the auto-selected
    profile (``$MEMORY_PROFILE`` override; else accuracy if ``$VOYAGE_API_KEY``
    is set; else fusion). MarkdownStore docs still land at
    ``<basedir>/markdown/memory/<item_id>.md`` — Router fan-out includes the
    markdown backend — plus vector + graph coverage that direct-MarkdownStore
    writes used to miss.
    """
    from cookbook_memory.core.contract import build_store
    # cast because mypy's follow_imports=silent loses the cross-package
    # return-type annotation; build_store is typed as -> MemoryStore at source.
    return cast(MemoryStore, build_store(str(basedir)))


def _emit_cli_resolved_event(hook_event_name: str | None) -> None:
    """Emit a ``daydream.cli_resolved`` event with version + script-path provenance (F4)."""
    from memeval.dreaming import engine as _engine_mod
    from memeval.dreaming.events import emit

    try:
        from importlib.metadata import version as _pkg_version
        package_version = _pkg_version("agent-memory-eval")
    except Exception:
        package_version = "unknown"

    emit(
        "daydream.cli_resolved",
        sys_executable=sys.executable,
        script_path=str(Path(sys.argv[0]).resolve()) if sys.argv and sys.argv[0] else "",
        package_version=package_version,
        engine_module_path=str(Path(_engine_mod.__file__).resolve()) if _engine_mod.__file__ else "",
        hook_event_name=hook_event_name,
    )


def _set_store_env(store_arg: Path | None) -> str | None:
    """Set MEMORY_STORE from --store and return the prior value for try/finally restore."""
    prev = os.environ.get("MEMORY_STORE")
    if store_arg is not None:
        os.environ["MEMORY_STORE"] = str(Path(store_arg).resolve())
    return prev


def _restore_store_env(store_arg: Path | None, prev: str | None) -> None:
    """Restore the prior MEMORY_STORE value (or unset) when --store was used."""
    if store_arg is None:
        return
    if prev is None:
        os.environ.pop("MEMORY_STORE", None)
    else:
        os.environ["MEMORY_STORE"] = prev


def _alert_openrouter_unset(events_emit: Any) -> None:
    """Emit the OPENROUTER_API_KEY-unset alert across stderr + log + diary (halliday F9).

    Called exactly once per invocation, BEFORE any engine work. Engine still
    runs and fail-opens; this just stops the silence. Diary event is the only
    observable signal in CC's async-Stop subprocess path where stderr may be
    captured-and-discarded by the parent.
    """
    sys.stderr.write(
        "daydream-cli: OPENROUTER_API_KEY is unset — memory extraction "
        "disabled (see .env.example). Run continues; writes will be empty.\n"
    )
    log.warning(
        "OPENROUTER_API_KEY unset — memory extraction disabled; see .env.example"
    )
    events_emit("daydream.openrouter_unset")


def _handle_daydream(args: argparse.Namespace) -> int:
    """Run one Daydream pass — reads stdin JSON, calls engine, fail-opens on every exception."""
    from memeval.dreaming import _state, engine
    from memeval.dreaming.events import emit as events_emit, event_context

    stdin_data = _read_stdin_json()
    session_id = args.session if args.session is not None else stdin_data.get("session_id")
    if args.log is not None:
        log_path: Path | None = args.log
    elif "transcript_path" in stdin_data:
        log_path = Path(str(stdin_data["transcript_path"]))
    else:
        log_path = None

    if not session_id or log_path is None:
        log.warning(
            "daydream-cli: no session_id/transcript_path from stdin or flags; fail-open exit 0"
        )
        return 0

    openrouter_unset = not os.environ.get("OPENROUTER_API_KEY")

    prev = _set_store_env(args.store)
    try:
        try:
            basedir = _state.resolve_basedir()
        except (KeyError, FileNotFoundError, ValueError) as exc:
            log.warning(
                "daydream-cli: MEMORY_STORE resolution failed (%s: %s); fail-open exit 0",
                type(exc).__name__, exc,
            )
            return 0

        try:
            with event_context(session_id=session_id, basedir=basedir):
                _emit_cli_resolved_event(stdin_data.get("hook_event_name"))
                if openrouter_unset:
                    _alert_openrouter_unset(events_emit)
                store = _make_store(basedir)
                engine.daydream(session_id=session_id, log_path=log_path, store=store)
            return 0
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            log.warning(
                "daydream-cli: engine raised %s: %s; fail-open exit 0",
                type(exc).__name__, exc,
            )
            return 0
    finally:
        _restore_store_env(args.store, prev)


def _handle_dream(args: argparse.Namespace) -> int:
    """Run night-dream consolidation — catches _DreamLockHeld + _UnsupportedFsError separately per ADR-021."""
    from memeval.dreaming import _state, worker
    from memeval.dreaming.events import emit

    prev = _set_store_env(args.store)
    try:
        try:
            basedir = _state.resolve_basedir()
            store = _make_store(basedir)
            worker.dream(store=store)
            return 0
        except (KeyboardInterrupt, SystemExit):
            raise
        except _state._DreamLockHeld:
            log.warning(
                "daydream-cli: another Dream sweep holds the basedir lock; fail-open exit 0"
            )
            emit("dream.lock_contended", basedir=str(basedir))
            return 0
        except _state._UnsupportedFsError as exc:
            log.warning(
                "daydream-cli: $MEMORY_STORE is on an unsupported network filesystem: %s; "
                "set DREAM_ALLOW_NETWORK_FS=1 to override; fail-open exit 0",
                exc,
            )
            emit("dream.unsupported_fs", basedir=str(basedir))
            return 0
        except NotImplementedError:
            log.warning(
                "daydream-cli: night consolidation not yet implemented; fail-open exit 0"
            )
            emit("daydream.dream_all_skipped", reason="NotImplementedError")
            return 0
        except Exception as exc:
            log.warning(
                "daydream-cli: dream --all raised %s: %s; fail-open exit 0",
                type(exc).__name__, exc,
            )
            emit("daydream.dream_all_error", error_type=type(exc).__name__)
            return 0
    finally:
        _restore_store_env(args.store, prev)


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns 0 on success/fail-open, 1 on argparse error. CC reserves exit 2 (ADR-018)."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except argparse.ArgumentError as exc:
        log.error("argparse error: %s", exc)
        return 1
    except SystemExit as exc:
        code = exc.code
        if code is None or code == 0:
            return 0
        return 1

    if args.subcommand is None:
        log.error("missing subcommand; available: daydream, dream")
        return 1

    if args.subcommand == "daydream":
        return _handle_daydream(args)
    if args.subcommand == "dream":
        return _handle_dream(args)
    return 1
