"""Locate and drive the **Cursor CLI** (`cursor-agent`) headlessly, parsing its
``--output-format stream-json`` output.

``run_cursor`` runs ``cursor-agent -p <prompt> --output-format stream-json --trust
[--approve-mcps] [--force] --model <m>`` in a working directory, with a sandbox
environment (HOME + CURSOR_DATA_DIR + CURSOR_API_KEY — see :mod:`sandbox`), parses
the NDJSON event stream, and returns the answer text, token usage, and the MCP
tool-calls the agent made (so the agent can attribute ``recall`` to the trajectory).

Verified event shapes (``docs/harnesses/06-cursor-cli.md``):

* ``system``  (subtype ``init``): cwd, model, ``session_id``, ``permissionMode``.
* ``user`` / ``assistant``: ``message.content[].text``.
* ``tool_call`` (subtype ``started``/``completed``): ``tool_call.mcpToolCall.args``
  with ``name`` (``<server>-<tool>``), ``providerIdentifier``, ``toolName``, ``args``.
* ``thinking`` (delta/completed): reasoning — ignored for accounting.
* ``result`` (subtype ``success``): ``result`` (answer), ``is_error``, ``session_id``,
  ``duration_ms``, ``usage`` (``inputTokens``/``outputTokens``/``cacheReadTokens``/
  ``cacheWriteTokens``).

The parser is tolerant of schema variation (unknown event types ignored; usage read
only from ``result``), exactly like the Claude parser.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .platform import CursorRuntime, detect


class CursorNotInstalled(RuntimeError):
    """Raised when the ``cursor-agent`` CLI can't be found."""


@dataclass(slots=True)
class CursorToolCall:
    """One MCP tool call the agent made (from a ``tool_call`` event)."""

    server: str               # providerIdentifier, e.g. "cookbook-memory"
    tool: str                 # toolName, e.g. "recall"
    wire_name: str            # the model-facing name, e.g. "cookbook-memory-recall"
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CursorResult:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    num_turns: int = 0
    tool_calls: list[CursorToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def find_cursor() -> Optional[str]:
    rt = detect()
    return rt.exe if rt else None


def require_runtime(runtime: Optional[CursorRuntime] = None) -> CursorRuntime:
    rt = runtime or detect()
    if rt is None:
        raise CursorNotInstalled(
            "The Cursor CLI (cursor-agent) was not found. Install it with "
            "`curl https://cursor.com/install -fsS | bash` (puts cursor-agent on PATH "
            "at ~/.local/bin), then re-run. Override the path with $CURSOR_AGENT_CLI."
        )
    return rt


def build_argv(
    runtime: CursorRuntime, prompt: str, *, model: Optional[str] = None,
    approve_mcps: bool = True, force: bool = False, trust: bool = True,
    workspace: Optional[str | Path] = None, plugin_dir: Optional[str | Path] = None,
) -> list[str]:
    """Build the ``cursor-agent`` argv for a headless run. Pure — unit-tested.

    ``--trust`` is always passed (the eval checkout is trusted by construction);
    ``--approve-mcps`` clears the per-run MCP server-load gate (incl. a bundle's MCP);
    ``--force`` lets tool calls (incl. edits + the recall tool) run without prompts on
    plugin/code turns; ``--plugin-dir`` loads the shipping cookbook-memory bundle (the
    ``recall`` MCP server) — Cursor's only install path."""
    argv = [runtime.exe, "-p", prompt, "--output-format", "stream-json"]
    if trust:
        argv.append("--trust")
    if approve_mcps:
        argv.append("--approve-mcps")
    if force:
        argv.append("--force")
    if plugin_dir:
        argv += ["--plugin-dir", str(plugin_dir)]
    if model:
        argv += ["--model", model]
    if workspace:
        argv += ["--workspace", str(workspace)]
    return argv


def _parse_stream_json(stdout: str) -> CursorResult:
    """Parse ``cursor-agent --output-format stream-json`` (one JSON object per line)."""
    last_result: dict[str, Any] = {}
    assistant_text_parts: list[str] = []
    tool_calls: list[CursorToolCall] = []
    tin = tout = turns = 0
    cost = 0.0
    seen_call_ids: set[str] = set()

    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        if etype == "assistant":
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            for item in msg.get("content") or []:
                if isinstance(item, dict) and item.get("type") == "text":
                    t = item.get("text")
                    if isinstance(t, str) and t:
                        assistant_text_parts.append(t)
        elif etype == "tool_call":
            # Only count a started call once (started+completed are two events).
            tc = _extract_tool_call(ev)
            if tc is not None:
                cid = str((ev.get("call_id") or ev.get("tool_call", {}).get("toolCallId") or ""))
                if cid and cid in seen_call_ids:
                    continue
                if cid:
                    seen_call_ids.add(cid)
                tool_calls.append(tc)
        elif etype == "result":
            last_result = ev
            usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else {}
            tin += int(usage.get("inputTokens", usage.get("input_tokens", 0)) or 0)
            tout += int(usage.get("outputTokens", usage.get("output_tokens", 0)) or 0)
            cost += float(ev.get("total_cost_usd", ev.get("cost_usd", 0.0)) or 0.0)
            turns += 1

    # Prefer the result event's answer; fall back to concatenated assistant text.
    text = ""
    for key in ("result", "text", "response", "content"):
        v = last_result.get(key)
        if isinstance(v, str) and v:
            text = v
            break
    if not text and assistant_text_parts:
        text = "\n".join(assistant_text_parts)

    return CursorResult(text=text, tokens_in=tin, tokens_out=tout, cost_usd=cost,
                        num_turns=turns, tool_calls=tool_calls, raw=last_result)


def _extract_tool_call(ev: dict[str, Any]) -> Optional[CursorToolCall]:
    """Pull an MCP tool call out of a ``tool_call`` event, matching on the structured
    ``mcpToolCall`` fields (``providerIdentifier``/``toolName``) rather than the joined
    wire string — robust to Cursor's version-sensitive name format (ADR-harness-015)."""
    tcall = ev.get("tool_call") if isinstance(ev.get("tool_call"), dict) else {}
    mcp = tcall.get("mcpToolCall") if isinstance(tcall.get("mcpToolCall"), dict) else {}
    args_blob = mcp.get("args") if isinstance(mcp.get("args"), dict) else {}
    if not args_blob:
        return None
    provider = str(args_blob.get("providerIdentifier") or "")
    tool = str(args_blob.get("toolName") or "")
    wire = str(args_blob.get("name") or (f"{provider}-{tool}" if provider and tool else ""))
    if not (provider or tool or wire):
        return None
    inner_args = args_blob.get("args") if isinstance(args_blob.get("args"), dict) else {}
    return CursorToolCall(server=provider, tool=tool, wire_name=wire, args=inner_args)


def run_cursor(
    prompt: str, *, cwd: str | Path, model: Optional[str] = None,
    approve_mcps: bool = True, force: bool = False, trust: bool = True,
    workspace: Optional[str | Path] = None, plugin_dir: Optional[str | Path] = None,
    timeout: int = 600, runtime: Optional[CursorRuntime] = None,
    env: Optional[dict[str, str]] = None,
) -> CursorResult:
    """Run one headless ``cursor-agent -p`` turn and return text + usage + tool calls.

    ``env`` MUST be the sandbox environment from :func:`sandbox.env_for` (HOME +
    CURSOR_DATA_DIR + CURSOR_API_KEY); passing ``None`` inherits the current process
    env (used only by callers that have already exported those). ``workspace`` sets
    ``--workspace`` so the agent's working dir is independent of the sandbox HOME.
    ``plugin_dir`` loads the cookbook-memory bundle (the ``recall`` MCP server)."""
    rt = require_runtime(runtime)
    argv = build_argv(rt, prompt, model=model, approve_mcps=approve_mcps,
                      force=force, trust=trust, workspace=workspace or cwd,
                      plugin_dir=plugin_dir)
    proc = subprocess.run(
        argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        env=env, stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"cursor-agent exited {proc.returncode}: {err[:400]}")
    return _parse_stream_json(proc.stdout)


__all__ = [
    "CursorNotInstalled", "CursorResult", "CursorToolCall", "find_cursor",
    "require_runtime", "build_argv", "run_cursor",
]
