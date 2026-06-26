"""Cursor CLI (`cursor-agent`) harness adapter — a sibling of ``memeval.claudecode``.

A second eval-harness backend (ADR-harness-013) that drives the **Cursor CLI**
(`cursor-agent`) over the same benchmarks Claude Code runs, through the same
harness-agnostic seams (``memeval.agent.AgentAdapter`` + ``run_agent`` + the shared
graders/cost/trajectory machinery). It shares NO Claude-specific code; it only reuses
the genuinely harness-agnostic pieces (and a handful of pure, harness-neutral prompt
helpers from ``claudecode.agent`` — ``_build_prompt`` etc., which contain no Claude
wiring).

Cursor specifics live here, confined to three thin modules (mirroring claudecode):

* :mod:`platform` — discover ``cursor-agent`` on PATH (``CURSOR_AGENT_CLI`` override).
* :mod:`cli`      — drive ``cursor-agent -p --output-format stream-json`` and parse
  the result (answer + token usage + tool_call observation).
* :mod:`sandbox`  — HOME-based config isolation + keychain-free ``CURSOR_API_KEY``
  auth (ADR-harness-014), and the ``mcp.json`` + approval-gate wiring
  (ADR-harness-015).

See [`docs/harnesses/06-cursor-cli.md`](../../../docs/harnesses/06-cursor-cli.md) for
the verified behavior these modules encode.
"""

from __future__ import annotations

from .agent import CursorCodeAgent

__all__ = ["CursorCodeAgent"]
