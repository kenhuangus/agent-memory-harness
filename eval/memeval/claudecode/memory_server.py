"""MCP stdio server exposing our memory to Claude Code — the plugin's engine.

Run by the plugin's ``.mcp.json`` as::

    python -m memeval.claudecode.memory_server --bundle <dir> --log <recall.jsonl> --k 5

Exposes two tools to the agent:

* ``memory_recall(query, k)``     — search memory; returns ranked notes.
* ``memory_remember(content, tags)`` — persist a new memory.

Backed by an OKF-backed :class:`~memeval.claudecode.service.MemoryService`, so the
agent's memory is a portable OKF bundle on disk, and every recall is logged for
the benchmark harness. Lazy-imports the optional ``mcp`` SDK (``pip install
memeval[claudecode]``).
"""

from __future__ import annotations

import argparse
from typing import Optional


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="memeval.claudecode.memory_server")
    ap.add_argument("--bundle", required=True, help="OKF bundle dir backing the memory store.")
    ap.add_argument("--log", default=None, help="JSONL retrieval log path (for metric attribution).")
    ap.add_argument("--k", type=int, default=5, help="Default retrieval depth.")
    ap.add_argument("--transport", choices=["stdio", "http", "sse"], default="stdio",
                    help="MCP transport. 'http' (streamable-http) or 'sse' run a local "
                         "server claude connects to by URL — this avoids the stdio "
                         "startup race that intermittently drops the server in headless "
                         "`claude -p`. 'stdio' (default) is spawned per invocation.")
    ap.add_argument("--host", default="127.0.0.1", help="Bind host for http/sse transport.")
    ap.add_argument("--port", type=int, default=8765, help="Bind port for http/sse transport.")
    args = ap.parse_args(argv)

    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dep
        raise SystemExit(
            "The memory plugin needs the MCP SDK: `pip install memeval[claudecode]` "
            f"(or `pip install mcp`). Import error: {exc}"
        )

    from ..okf import OKFStore
    from .service import MemoryService

    store = OKFStore(args.bundle)
    svc = MemoryService(store, log_path=args.log, default_k=args.k)
    mcp = FastMCP("memeval-memory", host=args.host, port=args.port)

    @mcp.tool()
    def memory_recall(query: str, k: Optional[int] = None) -> list[dict]:
        """Search persistent memory and return the most relevant notes (ranked)."""
        return svc.recall(query, k)

    @mcp.tool()
    def memory_remember(content: str, tags: Optional[list[str]] = None) -> str:
        """Save a new note to persistent memory. Returns its id."""
        return svc.remember(content, tags=tags)

    if args.transport == "http":
        mcp.run(transport="streamable-http")  # claude: {"type":"http","url":".../mcp"}
    elif args.transport == "sse":
        mcp.run(transport="sse")              # claude: {"type":"sse","url":".../sse"}
    else:
        mcp.run()                             # stdio (spawned per claude invocation)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
