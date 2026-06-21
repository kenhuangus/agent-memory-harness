"""The ``memory`` CLI — the human / ops surface over the plugin core.

One console script, several subcommands (mirroring the cross-harness design's
"one core, two surfaces": MCP for the model, a CLI for us):

* ``memory mcp``            — speak MCP over stdio (the model's recall/remember tools).
* ``memory query "<q>"``    — debug retrieval from the shell.
* ``memory remember "<c>"`` — write a memory from the shell.
* ``memory stats``          — summarize the events stream for a store.
* ``memory log``            — print recent events (the black-box-readable trail).
* ``memory reset``          — clear a store's events stream (fresh per-run isolation).

This CLI is for the human dev and the plugin's own hook scripts — **not** a back door
for the eval engine, which drives the plugin only through the ``claude`` CLI
(ADR-eval-001). Every command resolves its store from ``$MEMORY_STORE`` or ``--store``.
Output is JSON on stdout so commands compose in scripts.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .core import build_memory
from .core.config import Settings
from .core.events import EventStream


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser with all subcommands."""
    p = argparse.ArgumentParser(prog="memory", description="Cookbook Memory CLI.")
    p.add_argument("--store", help="Store path (overrides $MEMORY_STORE).")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("mcp", help="Run the MCP stdio server (the model's recall/remember).")

    q = sub.add_parser("query", help="Debug retrieval: search memory and print hits.")
    q.add_argument("query", help="The search query.")
    q.add_argument("-k", type=int, default=None, help="Number of hits (default $MEMORY_K or 5).")

    r = sub.add_parser("remember", help="Write a memory from the shell.")
    r.add_argument("content", help="The memory content to store.")
    r.add_argument("--tags", default="", help="Comma-separated tags.")

    sub.add_parser("stats", help="Summarize the events stream for a store.")
    lg = sub.add_parser("log", help="Print recent memory events (JSONL).")
    lg.add_argument("-n", type=int, default=20, help="Number of recent events (default 20).")
    sub.add_parser("reset", help="Clear the store's events stream.")
    return p


def _emit(obj: object) -> None:
    json.dump(obj, sys.stdout, indent=2)
    sys.stdout.write("\n")


def _cmd_mcp(args: argparse.Namespace) -> int:
    # Lazy import: the MCP SDK is an optional dependency; only `memory mcp` needs it.
    from .adapters.claude_code.mcp_server import run as run_mcp

    return run_mcp(store=args.store)


def _cmd_query(args: argparse.Namespace) -> int:
    mem = build_memory(store=args.store, k=args.k)
    hits = mem.recall(args.query, args.k)
    _emit({"query": args.query, "hits": [h.to_dict() for h in hits]})
    return 0


def _cmd_remember(args: argparse.Namespace) -> int:
    mem = build_memory(store=args.store)
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    mem_id = mem.remember(args.content, tags=tags)
    _emit({"id": mem_id, "stored": bool(mem_id)})
    return 0


def _events_for(args: argparse.Namespace) -> EventStream:
    settings = Settings.from_env(store=args.store)
    return EventStream(settings.events_path)


def _cmd_stats(args: argparse.Namespace) -> int:
    events = _events_for(args).read()
    by_op: dict[str, int] = {}
    for e in events:
        by_op[e.get("op", "?")] = by_op.get(e.get("op", "?"), 0) + 1
    _emit({"total": len(events), "by_op": by_op})
    return 0


def _cmd_log(args: argparse.Namespace) -> int:
    events = _events_for(args).read()
    for e in events[-args.n:]:
        sys.stdout.write(json.dumps(e) + "\n")
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    settings = Settings.from_env(store=args.store)
    cleared = False
    if settings.events_path and settings.events_path.is_file():
        settings.events_path.unlink()
        cleared = True
    _emit({"reset": cleared, "events_path": str(settings.events_path) if settings.events_path else None})
    return 0


_COMMANDS = {
    "mcp": _cmd_mcp,
    "query": _cmd_query,
    "remember": _cmd_remember,
    "stats": _cmd_stats,
    "log": _cmd_log,
    "reset": _cmd_reset,
}


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return _COMMANDS[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
