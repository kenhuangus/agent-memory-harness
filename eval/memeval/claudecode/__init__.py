"""Run the 5 benchmarks locally through the **Claude Code CLI**, comparing
Claude Code's *built-in* memory against *our* memory mechanism (a plugin).

Three memory modes (all drive the same `claude -p` headless agent):

* ``off``     — no memory (baseline): just ask the question.
* ``builtin`` — Claude Code's own memory: the task's prior sessions are written to
  a ``CLAUDE.md`` in the run dir, which Claude Code auto-loads as context.
* ``plugin``  — our memory: a Claude Code **plugin** registers an MCP server
  (:mod:`memeval.claudecode.memory_server`) exposing ``memory_recall`` /
  ``memory_remember`` tools backed by our :class:`~memeval.protocols.MemoryStore`
  (OKF-backed). The agent retrieves/writes through it; the server logs every
  retrieval so we still get the recency/relevancy/efficiency metrics.

Pieces:
  service.py        MemoryService — recall/remember + retrieval logging (pure, tested)
  memory_server.py  MCP stdio server wrapping MemoryService (the plugin's engine)
  cli.py            locate + drive the `claude` CLI headlessly (JSON output)
  agent.py          ClaudeCodeAgent — an AgentAdapter for the three modes
  run_bench.py      run a benchmark x mode and log to the results ledger
  plugin/           the installable Claude Code plugin (.claude-plugin + .mcp.json)

Everything here lazy-imports heavy/optional deps (the ``mcp`` SDK, the ``claude``
binary), so importing this package stays stdlib-only and the offline tests run
with an injected fake CLI runner.
"""

from __future__ import annotations

from .service import MemoryService
from .agent import ClaudeCodeAgent, MemoryMode

__all__ = ["MemoryService", "ClaudeCodeAgent", "MemoryMode"]
