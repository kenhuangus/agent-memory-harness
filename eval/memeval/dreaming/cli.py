"""daydream-cli console script — argparse skeleton for `daydream` and `dream --all`.

Engine wiring (memeval.dreaming.engine.daydream signature:
    daydream(*, session_id, log_path, store, client=None, basedir=None,
             now=None, id_gen=None) -> None
) lands in subsequent TDD passes; this module is the structural skeleton only.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import NoReturn

log = logging.getLogger(__name__)


class _NonExitingParser(argparse.ArgumentParser):
    """ArgumentParser that raises ArgumentError on parse failure instead of exiting with code 2."""

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


def _handle_daydream(args: argparse.Namespace) -> int:
    """Stub handler for the daydream subcommand; engine wiring lands in the impl phase."""
    log.info("daydream subcommand scaffold reached; engine wiring pending impl phase")
    return 0


def _handle_dream(args: argparse.Namespace) -> int:
    """Stub handler for the dream --all subcommand; night-dream wiring lands in the impl phase."""
    log.info("dream --all subcommand scaffold reached; night-dream wiring pending impl phase")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns 0 on success, 1 on argparse error. CC reserves exit 2; _NonExitingParser.error raises ArgumentError so main can map to exit 1."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except argparse.ArgumentError as exc:
        log.error("argparse error: %s", exc)
        return 1

    if args.subcommand is None:
        log.error("missing subcommand; available: daydream, dream")
        return 1

    from memeval.dreaming import engine  # noqa: F401  # REASON: lazy per arch §3 / rubric §B-11

    if args.subcommand == "daydream":
        return _handle_daydream(args)
    if args.subcommand == "dream":
        return _handle_dream(args)
    return 1
