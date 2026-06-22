"""Package entry point: ``python -m cookbook_memory`` → the ``memory-cli``.

The plugin's Claude Code bundle invokes its surfaces by **module** rather than by
console script — ``python3 -m cookbook_memory mcp`` for the MCP server and
``python3 -m cookbook_memory.adapters.claude_code.hooks_handler <Event>`` for hooks.
Running by module finds the package via the interpreter's own ``sys.path``, so it
works wherever ``cookbook_memory`` is importable, with no requirement that the
``memory-cli`` / ``memory-hook`` console scripts be on ``$PATH`` (those stay as
human conveniences). This module makes ``python -m cookbook_memory`` dispatch to the
CLI so the MCP-server invocation in ``.mcp.json`` resolves.
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
