"""MCP stdio server exposing ``recall`` to Claude Code.

The model-callable surface of the plugin. The conscious agent is **recall-only** — it
reads memory and never writes (all memory creation is the Daydreamer's,
asynchronously) — so this server exposes a single tool:

* ``recall(query, k)`` — search memory; returns ranked hits. Routed through the core's
  ``MemoryClient`` (engine → route · rank), fail-open.

Run by the plugin's ``.mcp.json`` (``memory-cli mcp``) over stdio — the transport
Claude Code speaks. The MCP SDK is an optional dependency; this module lazy-imports it
so the rest of the plugin imports cleanly without it installed.
"""

from __future__ import annotations

from typing import Optional

from ...core import MemoryClient


def run(*, store: Optional[str] = None) -> int:
    """Construct the core and serve the ``recall`` tool over MCP stdio.

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

    client = MemoryClient(store=store)
    server = FastMCP("cookbook-memory")

    @server.tool()
    def recall(query: str, k: int = 5) -> list[dict]:
        """Search persistent memory for relevant notes from past sessions; ranked hits.

        Call proactively before starting a task, editing a file, debugging a test, or
        deciding between approaches — a past session may have hit this code or recorded
        a relevant decision. Cheap and fail-open; empty results are fine. ``k`` caps
        hits (default 5).
        """
        return [h.to_dict() for h in client.recall(query, k)]

    server.run()  # stdio transport
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
