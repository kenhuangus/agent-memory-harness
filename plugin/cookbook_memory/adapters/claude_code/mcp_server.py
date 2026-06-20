"""MCP stdio server exposing ``recall`` / ``remember`` to Claude Code.

The model-callable surface of the plugin (ADR-harness-002). Two tools, both routed
through the core (Orchestrator → route · rank · dedup), both fail-open:

* ``recall(query, k)``       — search memory; returns ranked hits.
* ``remember(content, tags)`` — persist a memory; returns ``{id}``.

Run by the plugin's ``.mcp.json`` (``memory mcp``) over stdio — the transport Claude
Code speaks. The MCP SDK is an optional dependency; this module lazy-imports it so the
rest of the plugin imports cleanly without it installed.
"""

from __future__ import annotations

from typing import Optional

from ...core import build_memory


def run(*, store: Optional[str] = None) -> int:
    """Construct the core and serve the two tools over MCP stdio.

    ``store`` overrides ``$MEMORY_STORE``. Returns a process exit code. Raises a
    clear ``SystemExit`` if the MCP SDK isn't installed (it's the one hard dependency
    of this surface).
    """
    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dep
        raise SystemExit(
            "The memory MCP server needs the MCP SDK: `pip install cookbook-memory[mcp]` "
            f"(or `pip install mcp`). Import error: {exc}"
        )

    mem = build_memory(store=store)
    server = FastMCP("cookbook-memory")

    @server.tool()
    def recall(query: str, k: int = 5) -> list[dict]:
        """Search persistent memory; return the most relevant notes (ranked)."""
        return [h.to_dict() for h in mem.recall(query, k)]

    @server.tool()
    def remember(content: str, tags: Optional[list[str]] = None) -> dict:
        """Save a note to persistent memory. Returns its id (empty if unavailable)."""
        return {"id": mem.remember(content, tags=tags)}

    server.run()  # stdio transport
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
