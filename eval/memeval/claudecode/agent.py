"""ClaudeCodeAgent — drive the Claude Code CLI over a benchmark task.

An :class:`~memeval.agent.AgentAdapter` with three memory modes:

* ``off``     — ask the question with no memory (baseline).
* ``builtin`` — write the task's prior sessions to ``CLAUDE.md`` in the run dir;
  Claude Code auto-loads it. This benchmarks Claude Code's *built-in* memory.
* ``plugin``  — seed an OKF-backed store, point a ``.mcp.json`` at our memory
  server, and let the agent retrieve/write through the ``memory_*`` tools. The
  server logs retrievals so the harness still scores recency/relevancy/efficiency.

Works on macOS / Linux / Windows / Windows-WSL via
:mod:`memeval.claudecode.platform` (the ``.mcp.json`` is written with the right
python + path form for the detected runtime). The ``runner`` is injectable
(defaults to :func:`memeval.claudecode.cli.run_claude`) so the offline tests
exercise the wiring with a fake CLI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from ..cost import price_for
from ..schema import MemoryItem, RetrievedItem, Task
from .cli import ClaudeResult, run_claude
from .platform import ClaudeRuntime, detect, to_wsl_path
from .service import MemoryService

MemoryMode = str  # "off" | "builtin" | "plugin"
_MODES = ("off", "builtin", "plugin")

_SYS_PLUGIN = (
    "You have persistent memory via the memory_recall and memory_remember tools. "
    "ALWAYS call memory_recall with the question before answering, use the returned "
    "notes, and answer concisely with just the final answer."
)
_SYS_PLAIN = "Answer concisely with just the final answer."
# In headless -p mode the model follows a tool instruction in the USER prompt far
# more reliably than one only in the system prompt, so plugin mode prepends this.
_PLUGIN_PREFIX = (
    "First call the memory_recall tool with the question to retrieve relevant prior "
    "context, then answer concisely with just the final answer.\n\n"
)


class ClaudeCodeAgent:
    """Benchmark agent backed by the Claude Code CLI. Satisfies AgentAdapter."""

    def __init__(
        self,
        *,
        model: str = "claude-haiku-4-5",
        memory_mode: MemoryMode = "off",
        runner: Optional[Callable[..., ClaudeResult]] = None,
        runtime: Optional[ClaudeRuntime] = None,
        workdir: Optional[str | Path] = None,
        k: int = 5,
        timeout: int = 300,
    ) -> None:
        if memory_mode not in _MODES:
            raise ValueError(f"memory_mode must be one of {_MODES}, got {memory_mode!r}")
        self.model = model
        self.memory_mode = memory_mode
        self._runner = runner or run_claude
        self._runtime = runtime
        self._root = Path(workdir) if workdir else None
        self.k = k
        self.timeout = timeout
        self.name = f"claude-code:{model}:{memory_mode}"
        price = price_for(model)
        self.price_in = price["in"]
        self.price_out = price["out"]

    # -- AgentAdapter ------------------------------------------------------- #
    def solve(self, task: Task, ctx: Any, **_: Any) -> str:
        run_dir = self._task_dir(task)
        run_dir.mkdir(parents=True, exist_ok=True)
        prompt = _build_prompt(task)

        if self.memory_mode == "builtin":
            _write_claude_md(run_dir, task)
            res = self._run(prompt, run_dir, _SYS_PLAIN, mcp_config=None, allowed_tools=None)
        elif self.memory_mode == "plugin":
            res = self._solve_plugin(task, ctx, _PLUGIN_PREFIX + prompt, run_dir)
        else:  # off
            res = self._run(prompt, run_dir, _SYS_PLAIN, mcp_config=None, allowed_tools=None)

        ctx.record_generate(res.text, res.tokens_in, res.tokens_out, model_name=self.model)
        return res.text

    # -- plugin mode (our memory) ------------------------------------------ #
    def _solve_plugin(self, task: Task, ctx: Any, prompt: str, run_dir: Path) -> ClaudeResult:
        from ..okf import OKFStore  # local import: keeps package import light

        bundle = run_dir / "memory"
        log = run_dir / "recall.jsonl"
        store = OKFStore(bundle)
        for s in task.sessions:
            store.write(MemoryItem.from_session(s))

        rt = self._effective_runtime()
        # Write .mcp.json with the python + path form the detected runtime needs:
        # under WSL, claude spawns the server inside WSL, so paths must be /mnt/...
        # and the command a WSL python that has memeval + mcp.
        wsl = rt.kind == "wsl"
        bundle_arg = to_wsl_path(bundle) if wsl else str(bundle)
        log_arg = to_wsl_path(log) if wsl else str(log)
        mcp_path = run_dir / ".mcp.json"
        mcp_path.write_text(json.dumps({
            "mcpServers": {
                "memeval-memory": {
                    "command": rt.python,
                    "args": ["-m", "memeval.claudecode.memory_server",
                             "--bundle", bundle_arg, "--log", log_arg, "--k", str(self.k)],
                }
            }
        }), encoding="utf-8")

        tools = ["mcp__memeval-memory__memory_recall", "mcp__memeval-memory__memory_remember"]
        res = self._run(prompt, run_dir, _SYS_PLUGIN, mcp_config=mcp_path,
                        allowed_tools=tools, strict_mcp=True)

        # Attribute what the agent retrieved (from the server's log) to the trajectory.
        for rec in MemoryService.read_log(log):
            if rec.get("op") != "recall":
                continue
            hits = [
                RetrievedItem(
                    item=MemoryItem(
                        item_id=h["id"], content=h.get("content", ""),
                        timestamp=float(h.get("timestamp", 0.0) or 0.0),
                        tokens=int(h.get("tokens", 0) or 0),
                    ),
                    score=float(h.get("score", 0.0) or 0.0),
                    rank=int(h.get("rank", i) or i),
                )
                for i, h in enumerate(rec.get("hits", []))
            ]
            if hits:
                ctx.record_retrieve(hits, query=rec.get("query", ""))
        return res

    # -- helpers ------------------------------------------------------------ #
    def _run(self, prompt: str, cwd: Path, system: str, *,
             mcp_config: Optional[Path], allowed_tools: Optional[list[str]],
             strict_mcp: bool = False) -> ClaudeResult:
        return self._runner(
            prompt, cwd=cwd, model=self.model, mcp_config=mcp_config,
            allowed_tools=allowed_tools, append_system_prompt=system,
            strict_mcp=strict_mcp, strip_api_key=True,  # subscription only — never an API key
            timeout=self.timeout, runtime=self._runtime,
        )

    def _effective_runtime(self) -> ClaudeRuntime:
        """Runtime for writing .mcp.json. Falls back to a native default (offline
        tests have no claude installed) so the config is still produced."""
        return self._runtime or detect() or ClaudeRuntime(
            kind="native", exe="claude", python=sys.executable or "python")

    def _task_dir(self, task: Task) -> Path:
        import tempfile
        root = self._root or Path(tempfile.gettempdir()) / "memeval-claudecode"
        safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in str(task.task_id))[:80]
        return root / self.memory_mode / safe


def _build_prompt(task: Task) -> str:
    parts = [task.question.strip()]
    if task.choices:
        parts.append("Choices: " + " | ".join(task.choices))
    return "\n".join(parts)


def _write_claude_md(run_dir: Path, task: Task) -> None:
    """Render the task's prior sessions as Claude Code's built-in memory file."""
    from datetime import datetime, timezone

    lines = ["# Memory", "",
             "Earlier context you should use to answer questions in this project:", ""]
    for s in task.sessions:
        when = ""
        if s.timestamp:
            try:
                when = " — " + datetime.fromtimestamp(s.timestamp, tz=timezone.utc).date().isoformat()
            except Exception:
                when = ""
        lines.append(f"## {s.session_id}{when}")
        lines.append(s.content.strip())
        lines.append("")
    (run_dir / "CLAUDE.md").write_text("\n".join(lines), encoding="utf-8")


__all__ = ["ClaudeCodeAgent", "MemoryMode"]
