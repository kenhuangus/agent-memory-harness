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

import re

from ..cost import price_for
from ..schema import MemoryItem, RetrievedItem, Task, TaskKind
from .cli import ClaudeResult, run_claude, run_claude_primed
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
# CODE tasks: the model must emit a patch, not prose. _extract_diff() defends
# against the common case where it adds commentary or fences anyway.
_SYS_CODE = (
    "You are an automated software-engineering agent. Output ONLY a unified diff "
    "in git format that resolves the issue. Begin directly with 'diff --git'. Do "
    "NOT include any explanation, commentary, or markdown code fences."
)
# In headless -p mode the model follows a tool instruction in the USER prompt far
# more reliably than one only in the system prompt, so plugin mode prepends this.
_PLUGIN_PREFIX = (
    "First call the memory_recall tool with the question to retrieve relevant prior "
    "context, then answer concisely with just the final answer.\n\n"
)

# Builtin mode = Claude Code's OWN memory/context mechanism. The real one is not
# "dump the whole history into the context window" (a 200k+-token CLAUDE.md just
# 400s) — it is agentic retrieval: the prior history is written as files in the
# working dir and Claude Code uses its native tools (Grep/Glob/Read) to search and
# read only what it needs, plus its own context compaction. So we lay the history
# out as sessions/*.md and let Claude Code retrieve over the FULL history itself.
# Plugin mode instead retrieves through our MCP memory tools over the same full
# history — so the comparison is Claude Code's native memory vs our framework, both
# with complete information (no truncation, no artificial window cap).
_SYS_BUILTIN = (
    "Earlier conversation history for this project is stored as files under the "
    "sessions/ directory. Search and read those files (e.g. grep for keywords from "
    "the question) to find what you need, then answer concisely with just the final answer."
)
# In headless -p mode the model follows a tool instruction in the USER prompt more
# reliably than one only in the system prompt, so builtin mode prepends this too.
_BUILTIN_PREFIX = (
    "Earlier conversation history is in files under the sessions/ directory. Search/read "
    "them (grep for keywords from the question) to find what you need, then answer "
    "concisely with just the final answer.\n\n"
)

# Headless `claude -p` connects an MCP server only ~half the time per *plain* turn:
# the model starts generating before claude's async MCP connection finishes
# registering tools (a startup race), so `memory_recall` is silently unavailable.
# The fix (see cli.run_claude_primed) sends a trivial priming turn first over
# stream-json I/O — that turn gives the MCP connection a full turn to register, so
# the real turn reaches memory ~100% of the time (measured 20/20 vs 8/20 baseline).
# Retry-until-recall stays as a cheap backstop for the rare miss.
_PLUGIN_MAX_TRIES = 3


def _free_port() -> int:
    """Pick an OS-assigned free localhost port for a per-task HTTP memory server."""
    import socket

    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _wait_port(host: str, port: int, timeout: float = 20.0) -> bool:
    """Block until ``host:port`` accepts a connection (server ready), or timeout."""
    import socket
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.2)
    return False


def _count_recalls(log: Path) -> int:
    """Number of recall ops logged so far (used to detect the agent reached memory)."""
    try:
        return sum(1 for rec in MemoryService.read_log(log) if rec.get("op") == "recall")
    except Exception:
        return 0


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
        transport: str = "http",
    ) -> None:
        if memory_mode not in _MODES:
            raise ValueError(f"memory_mode must be one of {_MODES}, got {memory_mode!r}")
        self.model = model
        self.memory_mode = memory_mode
        self._runner = runner or run_claude
        self._runtime = runtime
        # Plugin MCP transport. "http" runs a local memory server claude connects to
        # by URL; combined with retry-until-recall this is reliable in headless mode,
        # where a freshly stdio-spawned server is dropped ~half the time (a race).
        # "stdio" keeps the spawn-per-invocation form (used by the offline tests and
        # as a fallback when claude runs across the Windows/WSL boundary).
        self.transport = transport
        # Resolve to absolute: claude runs with cwd=run_dir and resolves --mcp-config
        # (and the MCP server resolves its --bundle/--log) against that cwd, so a
        # relative run dir would double the path ("run_dir/run_dir/.mcp.json" -> not
        # found) and every plugin task would fail. Absolute paths are cwd-independent.
        self._root = Path(workdir).resolve() if workdir else None
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

        # CODE tasks need a unified diff, not a QA answer. Branch BEFORE the
        # memory_mode dispatch and run a single plain turn that asks for a diff,
        # then return the robustly-extracted diff (or '' for non-diff output).
        # QA tasks fall through to the EXACT untouched path below — byte-identical.
        if task.kind == TaskKind.CODE:
            code_prompt = _build_code_prompt(task)
            res = self._run(code_prompt, run_dir, _SYS_CODE,
                            mcp_config=None, allowed_tools=None)
            ctx.record_generate(res.text, res.tokens_in, res.tokens_out,
                                model_name=self.model)
            return _extract_diff(res.text)

        prompt = _build_prompt(task)

        if self.memory_mode == "builtin":
            _write_session_files(run_dir, task)
            res = self._run(_BUILTIN_PREFIX + prompt, run_dir, _SYS_BUILTIN,
                            mcp_config=None, allowed_tools=None)
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
        tools = ["mcp__memeval-memory__memory_recall", "mcp__memeval-memory__memory_remember"]

        if self.transport == "http" and rt.kind == "native":
            res = self._run_plugin_http(prompt, run_dir, bundle, log, rt, tools)
        else:
            # stdio: spawn-per-invocation. Used by offline tests (fake runner) and as a
            # fallback when claude runs across the Windows/WSL boundary.
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

    def _run_plugin_http(self, prompt: str, run_dir: Path, bundle: Path, log: Path,
                         rt: ClaudeRuntime, tools: list[str]) -> ClaudeResult:
        """Plugin via an HTTP memory server + a priming turn (with retry backstop).

        Headless ``claude -p`` starts generating before its async MCP connection
        finishes registering tools, so on a *plain* turn ``memory_recall`` is
        silently unavailable ~half the time. We run the memory server as a local
        HTTP service claude connects to by URL, then drive each turn through
        :func:`run_claude_primed`, which sends a trivial priming turn first
        (stream-json I/O) so the MCP connection is registered before the real
        question generates. Measured first-try recall: 20/20 (vs 8/20 plain).
        Retry-until-recall remains as a cheap backstop; the server stays up across
        retries. Falls back to a single attempt if the server never comes up.
        """
        import subprocess

        host, port = "127.0.0.1", _free_port()
        mcp_path = run_dir / ".mcp.json"
        mcp_path.write_text(json.dumps({
            "mcpServers": {"memeval-memory": {"type": "http", "url": f"http://{host}:{port}/mcp"}}
        }), encoding="utf-8")

        srv = subprocess.Popen(
            [rt.python, "-m", "memeval.claudecode.memory_server",
             "--transport", "http", "--host", host, "--port", str(port),
             "--bundle", str(bundle), "--log", str(log), "--k", str(self.k)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            if not _wait_port(host, port, timeout=20.0):
                # server never came up — one primed attempt so we still answer
                return self._run_primed(prompt, run_dir, _SYS_PLUGIN, mcp_config=mcp_path,
                                        allowed_tools=tools)
            res: Optional[ClaudeResult] = None
            for _ in range(_PLUGIN_MAX_TRIES):
                before = _count_recalls(log)
                res = self._run_primed(prompt, run_dir, _SYS_PLUGIN, mcp_config=mcp_path,
                                       allowed_tools=tools)
                if _count_recalls(log) > before:
                    break  # the agent reached memory_recall -> MCP connected
            return res  # type: ignore[return-value]
        finally:
            srv.terminate()
            try:
                srv.wait(timeout=5)
            except Exception:
                srv.kill()

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

    def _run_primed(self, prompt: str, cwd: Path, system: str, *,
                    mcp_config: Optional[Path], allowed_tools: Optional[list[str]]) -> ClaudeResult:
        """Like :meth:`_run`, but drives a priming turn first (stream-json I/O) so
        the MCP connection registers before the real prompt generates. If a custom
        runner was injected (offline tests), defer to it instead — the priming flow
        only matters against the real CLI."""
        runner = run_claude_primed if self._runner is run_claude else self._runner
        return runner(
            prompt, cwd=cwd, model=self.model, mcp_config=mcp_config,
            allowed_tools=allowed_tools, append_system_prompt=system,
            strict_mcp=True, strip_api_key=True,  # subscription only — never an API key
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


def _build_code_prompt(task: Task) -> str:
    """Build the user prompt for a CODE task: the issue text, the repo/base-commit
    context that exists on the Task (no checkout is provided), and a strict
    instruction to respond with ONLY a unified diff. Pure / offline-testable."""
    parts = [(task.question or "").strip()]
    if task.repo:
        parts.append(f"Repository: {task.repo}")
    if task.base_commit:
        parts.append(f"Base commit: {task.base_commit}")
    parts.append(
        "Respond with ONLY a unified diff (git 'diff --git' format) that fixes "
        "the issue. No prose, no code fences."
    )
    return "\n".join(parts)


# A line that opens a markdown code fence, with any optional language tag
# (```diff / ```patch / ```python / bare ```). Accepting any tag — not just
# diff/patch — means a diff wrapped in a mislabeled fence is still bounded by
# the fence body rather than leaking the closing fence + trailing prose.
_FENCE_OPEN_RE = re.compile(r"^\s*```+\s*\w*\s*$", re.IGNORECASE)
# A line that closes a markdown code fence (bare backticks).
_FENCE_CLOSE_RE = re.compile(r"^\s*```+\s*$")

# Prefixes that mark a line as part of a unified/git diff (used to tell a real
# diff continuation from trailing prose after a blank line).
_DIFF_PREFIXES = (
    "diff --git ", "index ", "--- ", "+++ ", "@@ ", "+", "-", " ", "\\",
    "old mode ", "new mode ", "new file mode ", "deleted file mode ",
    "similarity index ", "rename from ", "rename to ", "copy from ", "copy to ",
    "Binary files ", "GIT binary patch",
)


def _is_diff_line(line: str) -> bool:
    """True if ``line`` looks like part of a unified/git diff body."""
    return any(line.startswith(p) for p in _DIFF_PREFIXES)


def _extract_diff(text: str) -> str:
    """Turn arbitrary model output into a clean unified-diff string, or ''.

    Pure, deterministic, stdlib-only (so it is fully offline-testable). Steps:

    1. Empty / whitespace input -> ''.
    2. If a fenced code block (```diff / ```patch / bare ```) is present, take the
       body of the FIRST such block (handles the common case where the model adds
       fences despite the instruction not to). An unclosed fence -> everything
       after the opening line.
    3. Otherwise operate on the raw text.
    4. Anchor on the first line starting with 'diff --git ' (preferred). If none,
       fall back to the first '--- ' line, but ONLY when a later '+++ ' or '@@ '
       line also exists (so prose dashes are not mistaken for a diff). Slice from
       the anchor onward, dropping any leading prose.
    5. Trim trailing non-diff prose: when there is no fence to bound the diff, stop
       at the first blank line that is NOT followed by more diff content (so
       'diff...\\n\\nLet me know if this helps.' keeps only the diff). Then drop a
       stray closing fence and trailing blank lines, and ensure exactly one
       trailing newline (git apply is whitespace-sensitive at EOF).
    6. No diff marker anywhere -> '' (an honest empty patch, never prose).
    """
    if not text or not text.strip():
        return ""

    lines = text.splitlines()

    # (2) Prefer the first fenced block's body, if any fence opens.
    body_lines = lines
    fenced = False
    for i, line in enumerate(lines):
        if _FENCE_OPEN_RE.match(line):
            inner: list[str] = []
            for inner_line in lines[i + 1:]:
                if _FENCE_CLOSE_RE.match(inner_line):
                    break  # closing fence -> stop
                inner.append(inner_line)
            body_lines = inner
            fenced = True
            break

    # (4) Anchor on the first diff marker within the chosen body.
    start = None
    for idx, line in enumerate(body_lines):
        if line.startswith("diff --git "):
            start = idx
            break
    if start is None:
        for idx, line in enumerate(body_lines):
            if line.startswith("--- "):
                rest = body_lines[idx + 1:]
                if any(r.startswith("+++ ") or r.startswith("@@ ") for r in rest):
                    start = idx
                    break

    if start is None:
        return ""  # (6) no diff -> honest empty patch

    diff_lines = list(body_lines[start:])

    # (5) When the diff is not bounded by a fence, trim trailing prose: stop at the
    # first blank line that is not followed by more diff content. A fence already
    # bounds the body, so this step is skipped for fenced input.
    if not fenced:
        end = len(diff_lines)
        for j in range(len(diff_lines)):
            if diff_lines[j].strip():
                continue
            # blank line — keep it only if a later line still looks like a diff.
            if any(_is_diff_line(later) for later in diff_lines[j + 1:]):
                continue
            end = j
            break
        diff_lines = diff_lines[:end]

    # Strip a leaked trailing closing fence, then trailing blank lines.
    while diff_lines and _FENCE_CLOSE_RE.match(diff_lines[-1]):
        diff_lines.pop()
    while diff_lines and not diff_lines[-1].strip():
        diff_lines.pop()
    if not diff_lines:
        return ""
    return "\n".join(diff_lines) + "\n"


def _write_session_files(run_dir: Path, task: Task) -> None:
    """Lay the task's prior sessions out as files for Claude Code's native memory.

    Writes one Markdown file per session under ``sessions/`` plus a small
    ``CLAUDE.md`` pointer. Claude Code then uses its own tools (Grep/Glob/Read) to
    search and read only what it needs — its real context/memory mechanism — over
    the *full* history, with no truncation. (Contrast: dumping every session into
    one CLAUDE.md overflows the context window and just 400s.)
    """
    from datetime import datetime, timezone

    sess_dir = run_dir / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    # Clear any stale files from a previous run reusing this dir.
    for old in sess_dir.glob("*.md"):
        try:
            old.unlink()
        except OSError:
            pass

    for i, s in enumerate(task.sessions):
        when = ""
        if s.timestamp:
            try:
                when = datetime.fromtimestamp(s.timestamp, tz=timezone.utc).date().isoformat()
            except Exception:
                when = ""
        safe_id = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(s.session_id))[:60]
        fname = f"session_{i:04d}_{safe_id}.md"
        head = f"# Session {s.session_id}" + (f" ({when})" if when else "")
        (sess_dir / fname).write_text(f"{head}\n\n{s.content.strip()}\n", encoding="utf-8")

    (run_dir / "CLAUDE.md").write_text(
        "# Project memory\n\n"
        "Earlier conversation history for this project is stored as Markdown files "
        "under the `sessions/` directory (one file per session, named by order and id). "
        "To answer a question about earlier context, search those files (grep for "
        "keywords) and read the relevant ones before answering.\n",
        encoding="utf-8",
    )


__all__ = ["ClaudeCodeAgent", "MemoryMode"]
