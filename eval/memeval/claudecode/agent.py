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
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

import re

from ..cost import price_for
from ..schema import MemoryItem, RetrievedItem, Task, TaskKind
from . import checkout as _checkout
from .checkout import CheckoutError, GitRunner, capture_diff, prepare_checkout
from .cli import ClaudeResult, run_claude, run_claude_primed
from .platform import ClaudeRuntime, detect, to_wsl_path
from .service import MemoryService

MemoryMode = str  # "off" | "builtin" | "plugin" | "plugin-real"
# "plugin"      — our memory wired DIRECTLY by the harness (the memeval-memory MCP
#                 server + per-task .mcp.json). Fast, deterministic, in-process.
# "plugin-real" — the SHIPPING cookbook-memory plugin installed and driven exactly as
#                 a user installs it (native `claude plugin install` into the sandbox);
#                 a black-box end-to-end test of skill + MCP + hooks. See
#                 :meth:`ClaudeCodeAgent._solve_plugin_real`.
_MODES = ("off", "builtin", "plugin", "plugin-real")

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
# AGENTIC CODE mode: claude runs as a real coding agent in a working checkout —
# it reads the code with native tools, EDITS files directly, runs tests, and stops
# when the fix passes. The harness captures `git diff` as the prediction and grades
# it (LocalExecGrader), so the model must NOT print a diff or self-grade.
_SYS_CODE_AGENT = (
    "You are a software engineer working in a real checkout of the repository. "
    "Read the code with your tools, make the necessary edits to source files to "
    "resolve the issue, and run the project's tests to validate your change. "
    "Edit files directly — do NOT print a diff and do NOT paste patches into your "
    "reply. When the fix is complete and tests pass, stop."
)
# AGENTIC CODE *plugin* turn: same coding-agent contract as _SYS_CODE_AGENT, but
# the agent ALSO has our persistent memory (the memory_recall / memory_remember
# tools) and must consult it for prior fixes before editing — mirroring how
# _SYS_PLUGIN mandates recall for QA. Keeps every coding instruction (edit files
# directly, run tests, do NOT print a diff) so the CODE solve is unchanged.
_SYS_CODE_AGENT_PLUGIN = (
    "You are a software engineer working in a real checkout of the repository, with "
    "persistent memory via the memory_recall and memory_remember tools. BEFORE you "
    "start editing, call memory_recall with the issue text to retrieve prior fixes "
    "for this repository, and use what you recall. Then read the code with your "
    "tools, make the necessary edits to source files to resolve the issue, and run "
    "the project's tests to validate your change. Edit files directly — do NOT print "
    "a diff and do NOT paste patches into your reply. When the fix is complete and "
    "tests pass, stop."
)
# Headless follows a USER-prompt instruction more reliably than a system one, so
# the agentic CODE turn prepends this (mirrors _BUILTIN_PREFIX / _PLUGIN_PREFIX).
_CODE_AGENT_PREFIX = (
    "Edit the source files in this checkout directly to fix the issue, then run the "
    "tests to confirm. Do NOT output a diff or paste a patch — just make the edits.\n\n"
)
# In headless -p mode the model follows a tool instruction in the USER prompt far
# more reliably than one only in the system prompt, so plugin mode prepends this.
_PLUGIN_PREFIX = (
    "First call the memory_recall tool with the question to retrieve relevant prior "
    "context, then answer concisely with just the final answer.\n\n"
)
# AGENTIC CODE plugin turn: the QA-shaped _PLUGIN_PREFIX ("answer concisely")
# contradicts an edit-the-files coding task, so the CODE plugin turn uses this
# recall-then-EDIT prefix instead — the user-prompt counterpart of
# _SYS_CODE_AGENT_PLUGIN (headless follows a tool instruction in the user prompt
# more reliably than one only in the system prompt).
_PLUGIN_PREFIX_CODE = (
    "First call the memory_recall tool with the issue text to retrieve prior fixes "
    "for this repository, then edit the source files in this checkout directly to "
    "fix the issue and run the tests to confirm. Do NOT output a diff or paste a "
    "patch — just make the edits.\n\n"
)
# plugin-real mode uses the SHIPPING plugin, whose model-callable tool is `recall`
# (exposed by the cookbook-memory MCP server), not `memory_recall`. Same retrieve-
# then-answer instruction, named for the real tool.
_SYS_PLUGIN_REAL = (
    "You have persistent memory via the recall tool. ALWAYS call recall with the "
    "question before answering, use the returned notes, and answer concisely with "
    "just the final answer."
)
_PLUGIN_REAL_PREFIX = (
    "First call the recall tool with the question to retrieve relevant prior context, "
    "then answer concisely with just the final answer.\n\n"
)
# AGENTIC CODE *plugin-real* turn: same coding-agent contract as _SYS_CODE_AGENT,
# but the agent ALSO has the SHIPPING plugin's persistent memory. The shipping
# plugin's model-callable tool is `recall` (NOT `memory_recall`), so this prompt
# references `recall` — do NOT reuse _SYS_CODE_AGENT_PLUGIN, whose verb differs.
# Keeps every coding instruction (edit files directly, run tests, no diff) so the
# CODE solve is unchanged.
_SYS_CODE_AGENT_PLUGIN_REAL = (
    "You are a software engineer working in a real checkout of the repository, with "
    "persistent memory via the recall tool. BEFORE you start editing, call recall "
    "with the issue text to retrieve prior fixes for this repository, and use what "
    "you recall. Then read the code with your tools, make the necessary edits to "
    "source files to resolve the issue, and run the project's tests to validate your "
    "change. Edit files directly — do NOT print a diff and do NOT paste patches into "
    "your reply. When the fix is complete and tests pass, stop."
)
# AGENTIC CODE plugin-real turn: the QA-shaped _PLUGIN_REAL_PREFIX ("answer
# concisely") contradicts an edit-the-files coding task, so the CODE plugin-real
# turn uses this recall-then-EDIT prefix instead, naming the shipping plugin's
# `recall` tool (the user-prompt counterpart of _SYS_CODE_AGENT_PLUGIN_REAL).
_PLUGIN_REAL_PREFIX_CODE = (
    "First call the recall tool with the issue text to retrieve prior fixes for this "
    "repository, then edit the source files in this checkout directly to fix the "
    "issue and run the tests to confirm. Do NOT output a diff or paste a patch — "
    "just make the edits.\n\n"
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


def _read_events(events: Path) -> list[dict]:
    """Read the plugin's JSONL events stream (``events.jsonl``); ``[]`` if absent.

    The shipping cookbook-memory plugin owns its own events stream (ADR-harness-007),
    separate from the harness's ``MemoryService`` recall log — so plugin-real reads it
    directly rather than through ``MemoryService``."""
    try:
        return [json.loads(line) for line in events.read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return []


def _count_recall_events(events: Path) -> int:
    """Number of recall events in the plugin's own events stream."""
    return sum(1 for rec in _read_events(events) if rec.get("op") == "recall")


# Fix A — cross-task accumulation for the plugin-real store. SWE-Bench-CL runs the
# tasks of a sequence (``group_id``) in chronological order; the plugin's own
# learning (daydream writes via its real Stop-hook write path) must carry from task
# N to task N+1 so "improvement over time" is measurable. The store is COPIED into
# each task's per-task ``.cookbook-memory`` before the turn (restore) and copied
# back after (persist), EXCLUDING ``events.jsonl`` so per-task recall attribution
# (``_attribute_real_recall`` / ``_count_recall_events``) stays untouched.

#: Filename never copied between the group store and a per-task store — the plugin's
#: own recall/events stream is kept PER-TASK so recall attribution is not polluted by
#: a prior task's events. (The accumulated *memory* — markdown/vector/graph backends,
#: sidecars — copies; the events log does not.)
_GROUP_STORE_EXCLUDE = ("events.jsonl",)
#: Sentinel inside a group store marking that loader priors were already seeded for
#: this group (so ``_seed_plugin_store`` runs only once per group, on the first task).
_GROUP_SEEDED_SENTINEL = ".seeded"
#: How long to wait for the plugin's async Stop-hook daydream write to land in the
#: per-task events stream before the harness drives daydream synchronously itself.
#: Short by default; the real-run caller can raise it toward the Stop hook's 600s
#: ceiling via ``MEMEVAL_DAYDREAM_DRAIN_SECS``.
_DAYDREAM_DRAIN_DEFAULT_SECS = 8.0


def _copy_store_contents(src: Path, dst: Path,
                         exclude: tuple[str, ...] = _GROUP_STORE_EXCLUDE) -> None:
    """Copy every entry under ``src`` into ``dst`` (merge, overwrite), skipping any
    top-level name in ``exclude`` (e.g. ``events.jsonl``).

    Used to restore the accumulated group store INTO a per-task store and to persist
    a per-task store BACK to the group store. Recursive dirs are merged with
    ``dirs_exist_ok``. A missing ``src`` is a no-op (first task / nothing to copy)."""
    if not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        if entry.name in exclude:
            continue  # never carry the per-task recall/events stream across
        target = dst / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, target)


def _group_store_has_memory(group_store: Optional[Path]) -> bool:
    """True if ``group_store`` already holds accumulated memory (i.e. not the first
    task of the group). Any entry other than the seed sentinel counts — daydream
    writes land as markdown/vector/graph files + sidecars under the store root."""
    if group_store is None or not group_store.is_dir():
        return False
    return any(e.name != _GROUP_SEEDED_SENTINEL for e in group_store.iterdir())


#: Daydream events that mean "the plugin's write for this turn completed" — the engine
#: emits ``daydream.memory_written`` per item, and the hook shim emits
#: ``daydream.hook_subprocess_fired`` once the (blocking) subprocess returns.
_DAYDREAM_WRITE_OPS = ("daydream.memory_written", "daydream.hook_subprocess_fired")


def _count_daydream_writes(events: Path) -> int:
    """Number of daydream write-completion events in the plugin's events stream.

    The plugin's events use an ``event``/``type`` key for daydream diary events (vs the
    ``op`` key used for recall), so check both shapes to be robust to the stream
    schema."""
    n = 0
    for rec in _read_events(events):
        name = rec.get("event") or rec.get("type") or rec.get("op")
        if name in _DAYDREAM_WRITE_OPS:
            n += 1
    return n


def _drain_timeout_secs() -> float:
    """Daydream-drain poll window (seconds). ``MEMEVAL_DAYDREAM_DRAIN_SECS`` overrides
    the short default so a real run can wait toward the Stop hook's 600s ceiling."""
    raw = os.environ.get("MEMEVAL_DAYDREAM_DRAIN_SECS")
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return _DAYDREAM_DRAIN_DEFAULT_SECS


def _add_dream_env(extra_env: dict[str, str]) -> None:
    """Add the daydream WRITE-path env (``OPENROUTER_API_KEY`` + any ``DREAM_*``) to
    ``extra_env`` IN PLACE, from the process environment, when set.

    On WSL only ``extra_env`` crosses the Windows->WSL boundary (``_wsl_env_prefix``),
    so without this the in-WSL plugin Stop hook can't reach OpenRouter and its daydream
    write is empty. Never hardcodes a secret value — it forwards whatever is already in
    the environment, or nothing."""
    ork = os.environ.get("OPENROUTER_API_KEY")
    if ork:
        extra_env["OPENROUTER_API_KEY"] = ork
    for k, v in os.environ.items():
        if k.startswith("DREAM_"):
            extra_env[k] = v


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
        code_mode: str = "blind",
        git_runner: Optional[GitRunner] = None,
    ) -> None:
        if memory_mode not in _MODES:
            raise ValueError(f"memory_mode must be one of {_MODES}, got {memory_mode!r}")
        if code_mode not in ("blind", "agentic"):
            raise ValueError(f"code_mode must be 'blind' or 'agentic', got {code_mode!r}")
        self.model = model
        self.memory_mode = memory_mode
        # CODE-task strategy: "blind" = one turn that asks for a diff (no checkout);
        # "agentic" = claude edits a real checkout, `git diff` is the prediction,
        # the harness grades it. QA is unaffected by this switch.
        self.code_mode = code_mode
        self._runner = runner or run_claude
        # Injectable git seam for the agentic CODE path (mirrors the `runner`
        # injection). Defaults to the real subprocess git; offline tests inject a
        # fake that materializes the checkout + diff without network/git.
        self._git_runner = git_runner or _checkout._subprocess_git
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
        # plugin-real: built+installed once per agent; caches the MCP-server PATH env.
        self._real_plugin_env: Optional[dict[str, str]] = None
        self.k = k
        self.timeout = timeout
        self.name = f"claude-code:{model}:{memory_mode}"
        price = price_for(model)
        self.price_in = price["in"]
        self.price_out = price["out"]

    # -- AgentAdapter ------------------------------------------------------- #
    def solve(self, task: Task, ctx: Any, **_: Any) -> Any:
        run_dir = self._task_dir(task)
        run_dir.mkdir(parents=True, exist_ok=True)

        # CODE tasks need a code change, not a QA answer. Branch BEFORE the
        # memory_mode dispatch.
        #   agentic — claude edits a real checkout; `git diff` is the prediction
        #             and the harness (LocalExecGrader) owns the verdict.
        #   blind   — one plain turn asking for a diff, returned as a bare string
        #             (byte-identical to the prior behavior).
        # QA tasks fall through to the EXACT untouched path below.
        if task.kind == TaskKind.CODE:
            if self.code_mode == "agentic":
                return self._solve_code_agentic(task, ctx, run_dir)
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
        elif self.memory_mode == "plugin-real":
            res = self._solve_plugin_real(task, ctx, _PLUGIN_REAL_PREFIX + prompt, run_dir)
        else:  # off
            res = self._run(prompt, run_dir, _SYS_PLAIN, mcp_config=None, allowed_tools=None)

        ctx.record_generate(res.text, res.tokens_in, res.tokens_out, model_name=self.model)
        return res.text

    # -- plugin mode (our memory) ------------------------------------------ #
    def _seed_plugin_store_okf(self, run_dir: Path, task: Task) -> tuple[Path, Path, list[str]]:
        """Seed an OKF-backed store from the task's sessions for plugin MCP mode.

        Returns ``(bundle, log, tools)``. Pure seed step extracted so BOTH the QA
        plugin path and the agentic CODE path can reuse it (no behavior change)."""
        from ..okf import OKFStore  # local import: keeps package import light

        bundle = run_dir / "memory"
        log = run_dir / "recall.jsonl"
        store = OKFStore(bundle)
        for s in task.sessions:
            store.write(MemoryItem.from_session(s))
        tools = ["mcp__memeval-memory__memory_recall", "mcp__memeval-memory__memory_remember"]
        return bundle, log, tools

    def _attribute_plugin_recalls(self, log: Path, ctx: Any) -> None:
        """Attribute every recall in the server's log to the trajectory.

        Extracted from ``_solve_plugin`` so the agentic CODE path can record the
        same retrieve steps (closes the "CODE bypasses memory" gap). No behavior
        change — same logic, same RetrievedItem shape."""
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

    def _solve_plugin(self, task: Task, ctx: Any, prompt: str, run_dir: Path) -> ClaudeResult:
        bundle, log, tools = self._seed_plugin_store_okf(run_dir, task)

        rt = self._effective_runtime()

        if self.transport == "http" and rt.kind == "native":
            res = self._run_plugin_http(prompt, run_dir, bundle, log, rt, tools)
        else:
            # stdio: spawn-per-invocation. Used by offline tests (fake runner) and as a
            # fallback when claude runs across the Windows/WSL boundary.
            mcp_path = self._write_plugin_stdio_mcp(run_dir, bundle, log, rt)
            res = self._run(prompt, run_dir, _SYS_PLUGIN, mcp_config=mcp_path,
                            allowed_tools=tools, strict_mcp=True)

        # Attribute what the agent retrieved (from the server's log) to the trajectory.
        self._attribute_plugin_recalls(log, ctx)
        return res

    def _write_plugin_stdio_mcp(self, run_dir: Path, bundle: Path, log: Path,
                                rt: ClaudeRuntime) -> Path:
        """Write the stdio ``.mcp.json`` pointing at the spawn-per-invocation memory
        server (WSL-path-aware). Extracted so the CODE path reuses identical wiring."""
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
        return mcp_path

    # -- agentic CODE mode (real checkout; harness grades) ------------------ #
    def _solve_code_agentic(self, task: Task, ctx: Any, run_dir: Path) -> Any:
        """Drive ``claude`` as a real coding agent in a working checkout.

        Steps: (1) materialize a checkout of ``task.repo`` @ ``base_commit``;
        (2) wire memory per ``self.memory_mode`` so CODE finally records ``retrieve``
        steps (reusing the existing builtin/plugin seeding + attribution — the
        memory mechanism is untouched); (3) run claude in the checkout with the full
        native toolset (Read/Edit/Bash) so it edits files directly; (4) capture
        ``git diff`` as the prediction; (5) return an :class:`AgentResult` with
        ``success=None`` so the HARNESS grader — never the model — owns the verdict.

        Never crashes the run: a checkout failure leaves the task ungraded
        (``success=None``) with an empty prediction.
        """
        from ..agent import AgentResult  # local import: avoids any import-order coupling

        checkout = run_dir / "repo"
        try:
            prepare_checkout(task.repo or "", task.base_commit, checkout,
                             git_runner=self._git_runner, timeout=self.timeout)
        except CheckoutError as exc:
            ctx.note(f"checkout failed: {str(exc)[:200]}")
            return AgentResult(prediction="", patch="", success=None)

        # (2) memory wiring + (3) the coding turn, both keyed on memory_mode. Each
        # branch reuses the existing seed + recall-attribution; the agentic prompt
        # asks claude to EDIT files (not print a diff).
        base_prompt = _build_code_agent_prompt(task)
        res = self._run_code_agent_turn(task, ctx, base_prompt, run_dir, checkout)

        # (4) the captured diff IS the prediction.
        diff = capture_diff(checkout, base_commit=task.base_commit,
                            git_runner=self._git_runner)

        ctx.record_generate(res.text, res.tokens_in, res.tokens_out,
                            model_name=self.model)
        # (5) success=None -> the harness grader decides. NEVER self-grade.
        return AgentResult(prediction=diff, patch=diff, success=None)

    def _run_code_agent_turn(self, task: Task, ctx: Any, base_prompt: str,
                             run_dir: Path, checkout: Path) -> ClaudeResult:
        """Run the coding turn in ``checkout`` with memory wired per ``memory_mode``.

        Full native toolset (``allowed_tools=None`` -> ``--allowedTools`` omitted)
        and ``permission_mode="acceptEdits"`` so the agent reads/edits/runs against
        real files. Reuses the existing seeding + recall attribution so CODE records
        ``retrieve`` steps; the memory mechanism itself is unchanged."""
        mode = self.memory_mode
        if mode == "builtin":
            # Lay the history out as files in the CHECKOUT so the agent greps them.
            _write_session_files(checkout, task)
            return self._run(_BUILTIN_PREFIX + base_prompt, checkout, _SYS_CODE_AGENT,
                             mcp_config=None, allowed_tools=None,
                             permission_mode="acceptEdits")
        if mode == "plugin":
            bundle, log, tools = self._seed_plugin_store_okf(run_dir, task)
            rt = self._effective_runtime()
            mcp_path = self._write_plugin_stdio_mcp(checkout, bundle, log, rt)
            # Drive the coding turn through the PRIMED runner with a retry-until-recall
            # backstop (mirrors _run_plugin_http) so the headless-stdio MCP startup
            # race no longer silently drops memory_recall (~half of plain turns). We
            # use _run_primed directly — NOT _run_plugin_http, which is native-gated
            # (line below) and never runs on WSL; _run_primed supports WSL via
            # build_argv_primed and gates priming on `self._runner is run_claude`.
            # allowed_tools=tools allowlists the memory MCP tool; permission_mode is
            # kept at acceptEdits so the agent can still edit files (critical — the
            # whole CODE solve breaks otherwise).
            res: Optional[ClaudeResult] = None
            for _ in range(_PLUGIN_MAX_TRIES):
                before = _count_recalls(log)
                res = self._run_primed(
                    _PLUGIN_PREFIX_CODE + base_prompt, checkout, _SYS_CODE_AGENT_PLUGIN,
                    mcp_config=mcp_path, allowed_tools=tools, strict_mcp=True,
                    permission_mode="acceptEdits")
                if _count_recalls(log) > before:
                    break  # the agent reached memory_recall -> MCP connected
            self._attribute_plugin_recalls(log, ctx)
            return res  # type: ignore[return-value]
        if mode == "plugin-real":
            plugin_env = self._ensure_real_plugin()
            store_dir = checkout / ".cookbook-memory"
            store_dir.mkdir(parents=True, exist_ok=True)
            # Fix A: restore the group's accumulated memory into this task's store and
            # seed loader priors once per group (the SWE-Bench-CL accumulation path).
            # The store lives under the per-task CHECKOUT (it differs per base_commit),
            # but the GROUP store is keyed on group_id under the run-tree root, so
            # learning carries from task N to N+1. No group_id -> per-task seed only.
            group_store = self._group_restore(task, store_dir, plugin_env)
            extra_env = {**plugin_env, "CLAUDE_PROJECT_DIR": str(checkout)}
            _add_dream_env(extra_env)  # OPENROUTER_API_KEY / DREAM_* for the daydream write path
            events = store_dir / "events.jsonl"
            # Drive the coding turn through the PRIMED runner with a retry-until-recall
            # backstop (mirrors the QA _solve_plugin_real loop) so the headless MCP
            # startup race no longer silently drops the shipping plugin's `recall`.
            # allowed_tools stays None (full native toolset) — the shipping plugin
            # provides its own MCP tools via its installed .mcp.json under
            # CLAUDE_PROJECT_DIR; permission_mode stays acceptEdits so the agent can
            # still edit files. Recall reach is counted via the plugin's OWN events
            # stream (_count_recall_events), not the harness recall log.
            before_writes = _count_daydream_writes(events)
            res: Optional[ClaudeResult] = None
            for _ in range(_PLUGIN_MAX_TRIES):
                before = _count_recall_events(events)
                res = self._run_primed(
                    _PLUGIN_REAL_PREFIX_CODE + base_prompt, checkout,
                    _SYS_CODE_AGENT_PLUGIN_REAL, mcp_config=None, allowed_tools=None,
                    permission_mode="acceptEdits", extra_env=extra_env)
                if _count_recall_events(events) > before:
                    break  # the agent reached the recall tool -> plugin MCP connected
            self._attribute_real_recall(events, ctx)
            # Fix A: drain the plugin's async daydream WRITE, then persist this task's
            # learned memory back to the group store (excluding events.jsonl).
            self._drain_daydream(task, res, store_dir, events, plugin_env, before_writes)
            self._group_persist(group_store, store_dir)
            return res  # type: ignore[return-value]
        # off: no seeding, no memory.
        return self._run(_CODE_AGENT_PREFIX + base_prompt, checkout, _SYS_CODE_AGENT,
                         mcp_config=None, allowed_tools=None,
                         permission_mode="acceptEdits")

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

    # -- plugin-real mode (the shipping plugin, black box) ------------------ #
    def _solve_plugin_real(self, task: Task, ctx: Any, prompt: str,
                           run_dir: Path) -> ClaudeResult:
        """Run a task against the REAL cookbook-memory plugin, installed natively.

        Black box, end to end, exactly as a user runs it:

        1. ensure the plugin is built + installed into the sandbox once per agent
           (native ``claude plugin install``), capturing the PATH the MCP server needs;
        2. seed the plugin's store at ``<run_dir>/.cookbook-memory`` through the
           plugin's OWN CLI (``memory-cli remember``) — the same write surface a user
           or the Daydreamer uses — so nothing reaches into the plugin's Python API;
        3. drive a primed ``claude`` turn with ``CLAUDE_PROJECT_DIR=<run_dir>`` so the
           plugin's ``.mcp.json`` (``${CLAUDE_PROJECT_DIR}/.cookbook-memory``) resolves
           to the seeded store;
        4. attribute what the agent recalled to the trajectory, read from the plugin's
           own events stream (``events.jsonl``, ``meta.hits``).

        Fix A — when ``task.group_id`` is set, the plugin's own learning ACCUMULATES
        across the group: the accumulated store is copied in before the turn and the
        post-turn (post-daydream) store is copied back, EXCLUDING ``events.jsonl`` so
        recall attribution stays per-task. A task with no ``group_id`` keeps the exact
        prior behavior (per-task store only).
        """
        plugin_env = self._ensure_real_plugin()  # install first so memory-cli is on PATH
        store_dir = run_dir / ".cookbook-memory"
        store_dir.mkdir(parents=True, exist_ok=True)
        # Fix A: restore the group's accumulated memory into this per-task store and
        # seed loader priors once per group (no group_id -> per-task seed, as before).
        group_store = self._group_restore(task, store_dir, plugin_env)

        extra_env = {**plugin_env, "CLAUDE_PROJECT_DIR": str(run_dir)}
        _add_dream_env(extra_env)  # OPENROUTER_API_KEY / DREAM_* so daydream is live on WSL too
        events = store_dir / "events.jsonl"

        before_writes = _count_daydream_writes(events)
        res: Optional[ClaudeResult] = None
        for _ in range(_PLUGIN_MAX_TRIES):
            before = _count_recall_events(events)
            res = self._run_primed(prompt, run_dir, _SYS_PLUGIN_REAL,
                                   mcp_config=None, allowed_tools=None, extra_env=extra_env)
            if _count_recall_events(events) > before:
                break  # the agent reached the recall tool -> plugin MCP connected
        self._attribute_real_recall(events, ctx)
        # Fix A: drain the plugin's async daydream WRITE, then persist this task's
        # learned memory back to the group store (excluding events.jsonl).
        self._drain_daydream(task, res, store_dir, events, plugin_env, before_writes)
        self._group_persist(group_store, store_dir)
        return res  # type: ignore[return-value]

    def _ensure_real_plugin(self) -> dict[str, str]:
        """Build + install the real plugin into the sandbox once per agent; cache the
        PATH env its MCP server needs. Offline tests inject a runner, so skip the real
        install when no real CLI is in play."""
        if self._real_plugin_env is not None:
            return self._real_plugin_env
        if self._runner is not run_claude:
            self._real_plugin_env = {}      # offline/fake-runner: nothing to install
            return self._real_plugin_env
        from . import sandbox
        self._real_plugin_env = sandbox.setup_real_plugin(
            claude_exe=(self._runtime.exe if self._runtime else None))
        return self._real_plugin_env

    def _seed_plugin_store(self, task: Task, store_dir: Path,
                           plugin_env: dict[str, str]) -> None:
        """Seed the plugin's store through its own ``memory-cli remember`` — the real
        user/Daydreamer write surface (no reach into the plugin's Python API).

        Resolves ``memory-cli`` from the plugin runtime env (its install puts it on
        PATH); a no-op when it isn't found, so offline tests with a fake runner (no
        real install) skip seeding rather than fail."""
        import shutil
        import subprocess

        env = {**os.environ, **plugin_env, "MEMORY_STORE": str(store_dir)}
        exe = shutil.which("memory-cli", path=env.get("PATH"))
        if exe is None:
            return  # no real plugin install (e.g. offline test) — nothing to seed
        for s in task.sessions:
            item = MemoryItem.from_session(s)
            tags = ",".join(getattr(item, "tags", []) or [])
            args = [exe, "remember", item.content]
            if tags:
                args += ["--tags", tags]
            subprocess.run(args, env=env, capture_output=True, text=True,
                           timeout=60, check=False)

    # -- plugin-real cross-task group store (Fix A) ------------------------- #
    def _group_restore(self, task: Task, store_dir: Path,
                       plugin_env: dict[str, str]) -> Optional[Path]:
        """Copy the accumulated group store INTO this task's per-task ``store_dir`` and
        seed loader priors at most once per group. Returns the group store path (or
        ``None`` when the task has no ``group_id`` — then behavior is unchanged: the
        per-task store is seeded as before).

        Ordering: restore accumulated memory first (so a later task starts from what
        prior tasks learned), EXCLUDING ``events.jsonl``; then seed ``task.sessions``
        priors only on the FIRST task of the group (sentinel-guarded). For SWE-Bench-CL
        the first task usually has no priors, so seeding is effectively empty and the
        store builds purely from daydream — intended."""
        group_store = self._plugin_group_store(task)
        if group_store is None:
            # No group: per-task store only — seed priors exactly as before.
            self._seed_plugin_store(task, store_dir, plugin_env)
            return None

        first_task = not _group_store_has_memory(group_store)
        # Restore accumulated memory into the per-task store (no-op on the first task).
        _copy_store_contents(group_store, store_dir)
        # Seed loader priors ONCE per group (sentinel guards re-seeding on task N>1).
        seeded_sentinel = group_store / _GROUP_SEEDED_SENTINEL
        if first_task and not seeded_sentinel.exists():
            self._seed_plugin_store(task, store_dir, plugin_env)
            group_store.mkdir(parents=True, exist_ok=True)
            seeded_sentinel.write_text("", encoding="utf-8")
        return group_store

    def _group_persist(self, group_store: Optional[Path], store_dir: Path) -> None:
        """Copy this task's per-task store BACK to the group store (persist what the
        plugin learned this task), EXCLUDING ``events.jsonl`` so recall attribution
        stays per-task. No-op when the task has no group."""
        if group_store is None:
            return
        _copy_store_contents(store_dir, group_store)

    def _drain_daydream(self, task: Task, res: ClaudeResult, store_dir: Path,
                        events: Path, plugin_env: dict[str, str],
                        before_writes: int) -> None:
        """Ensure the plugin's async Stop-hook daydream WRITE has landed before the
        per-task store is persisted to the group store.

        The shipping plugin's ``Stop`` hook is ``async: true`` and shells out to
        ``daydream-cli daydream`` (which BLOCKS until the engine finishes) — but the CC
        process may return from the headless turn before that subprocess completes, so
        a naive copy-out would miss this task's new memory. Two-stage drain:

        1. **Poll** the per-task ``events.jsonl`` for a NEW daydream write event
           (``daydream.memory_written`` or ``daydream.hook_subprocess_fired``) for up to
           ``MEMEVAL_DAYDREAM_DRAIN_SECS`` (default :data:`_DAYDREAM_DRAIN_DEFAULT_SECS`).
           A fresh event means the hook's write already completed — done.
        2. **Backstop** — if no event lands in time, drive ``daydream-cli daydream``
           SYNCHRONOUSLY ourselves (same engine the hook uses) with the hook's stdin
           payload (``session_id`` + ``transcript_path`` discovered from the just-run
           turn) and env ``MEMORY_STORE=<store_dir>`` (+ ``OPENROUTER_API_KEY`` /
           ``DREAM_*`` when set). This guarantees the write completes before copy-out.

        No-op under the offline fake runner (no real ``daydream-cli`` / transcript);
        the drain only matters against the real CLI + plugin. If the transcript path
        cannot be discovered, the synchronous backstop is skipped (poll-only) — see the
        comment at the discovery site."""
        # Offline / fake-runner: nothing real to drain. _ensure_real_plugin returns {}
        # in that case (no install), which is the cheapest signal that this is a test.
        if self._runner is not run_claude:
            return

        deadline = time.monotonic() + _drain_timeout_secs()
        while time.monotonic() < deadline:
            if _count_daydream_writes(events) > before_writes:
                return  # the async hook's write already landed — drained.
            time.sleep(0.25)

        # Backstop: the hook didn't finish in time (or never fired) — run the SAME
        # daydream engine synchronously so the per-task memory is written before we
        # persist it to the group store.
        session_id, transcript = self._discover_transcript(res, store_dir)
        if not session_id or transcript is None:
            # Transcript path could not be reliably determined for this headless turn
            # (e.g. the session_id wasn't in the result envelope, or no matching
            # <session_id>.jsonl exists under the sandbox projects tree). Drain is
            # POLL-ONLY here — we do NOT silently pretend it drained. Documented
            # limitation: a slow async hook past the poll window may not be captured
            # for this task; raise MEMEVAL_DAYDREAM_DRAIN_SECS on the real run.
            return
        self._run_daydream_cli(session_id, transcript, store_dir, plugin_env)

    def _discover_transcript(self, res: ClaudeResult,
                             store_dir: Path) -> tuple[Optional[str], Optional[Path]]:
        """Best-effort (session_id, transcript_path) for the just-completed headless
        turn, for the synchronous daydream backstop.

        ``claude -p``'s JSON/stream-json result envelope carries ``session_id`` (kept on
        :attr:`ClaudeResult.raw`). Claude Code records that turn's transcript at
        ``<CLAUDE_CONFIG_DIR>/projects/<cwd-slug>/<session_id>.jsonl``. The slug
        encoding is CC-internal and platform-dependent (and differs under WSL), so
        rather than reconstruct it we GLOB for ``<session_id>.jsonl`` anywhere under the
        sandbox ``projects/`` tree — robust to the slug format. Returns ``(None, None)``
        when the session_id or the file can't be found."""
        raw = res.raw if isinstance(res.raw, dict) else {}
        session_id = raw.get("session_id") or raw.get("sessionId")
        if not session_id:
            return None, None
        cfg = sandbox.active_config_dir()
        if not cfg:
            return None, None
        projects = Path(cfg) / "projects"
        if not projects.is_dir():
            return str(session_id), None
        matches = list(projects.glob(f"**/{session_id}.jsonl"))
        if not matches:
            return str(session_id), None
        # Prefer the newest match if the id somehow appears twice.
        transcript = max(matches, key=lambda p: p.stat().st_mtime)
        return str(session_id), transcript

    def _run_daydream_cli(self, session_id: str, transcript: Path, store_dir: Path,
                          plugin_env: dict[str, str]) -> None:
        """Drive ``daydream-cli daydream`` synchronously — the SAME engine the plugin's
        Stop hook uses — with the hook's stdin payload and ``MEMORY_STORE=<store_dir>``.

        Passes ``OPENROUTER_API_KEY`` and any ``DREAM_*`` vars through from the process
        environment when set (the daydream write path needs them); never hardcodes a
        secret. No-op if ``daydream-cli`` isn't on PATH (so this stays safe even outside
        a full real install)."""
        import subprocess

        env = {**os.environ, **plugin_env, "MEMORY_STORE": str(store_dir)}
        ork = os.environ.get("OPENROUTER_API_KEY")
        if ork:
            env["OPENROUTER_API_KEY"] = ork
        for k, v in os.environ.items():
            if k.startswith("DREAM_"):
                env[k] = v
        exe = shutil.which("daydream-cli", path=env.get("PATH"))
        if exe is None:
            return  # no daydream-cli (e.g. partial install) — poll-only drain stands.
        payload = json.dumps({
            "session_id": session_id,
            "transcript_path": str(transcript),
            "hook_event_name": "Stop",
        })
        try:
            subprocess.run([exe, "daydream"], input=payload, env=env,
                           capture_output=True, text=True, timeout=self.timeout,
                           check=False)
        except Exception:
            return  # fail-open: a backstop failure must not crash the benchmark run.

    def _attribute_real_recall(self, events: Path, ctx: Any) -> None:
        """Record each recall the agent performed (from the plugin's events stream)
        to the trajectory, using the enriched ``meta.hits`` (ADR-harness-007)."""
        for rec in _read_events(events):
            if rec.get("op") != "recall":
                continue
            hits = [
                RetrievedItem(
                    item=MemoryItem(
                        item_id=str(h.get("id", "")), content=h.get("content", ""),
                        timestamp=float(h.get("timestamp", 0.0) or 0.0),
                        tokens=int(h.get("tokens", 0) or 0),
                    ),
                    score=float(h.get("score", 0.0) or 0.0),
                    rank=int(h.get("rank", i) or i),
                )
                for i, h in enumerate(rec.get("meta", {}).get("hits", []))
            ]
            if hits:
                ctx.record_retrieve(hits, query=rec.get("query", ""))

    # -- helpers ------------------------------------------------------------ #
    def _run(self, prompt: str, cwd: Path, system: str, *,
             mcp_config: Optional[Path], allowed_tools: Optional[list[str]],
             strict_mcp: bool = False, permission_mode: str = "bypassPermissions",
             extra_env: Optional[dict[str, str]] = None) -> ClaudeResult:
        # permission_mode/extra_env default to the prior behavior so QA/blind call
        # sites are unchanged; the agentic CODE path passes acceptEdits (+ plugin
        # env). The offline fake runners take **kw, so they swallow the new kwargs.
        return self._runner(
            prompt, cwd=cwd, model=self.model, mcp_config=mcp_config,
            allowed_tools=allowed_tools, append_system_prompt=system,
            strict_mcp=strict_mcp, strip_api_key=True,  # subscription only — never an API key
            timeout=self.timeout, runtime=self._runtime,
            permission_mode=permission_mode, extra_env=extra_env,
        )

    def _run_primed(self, prompt: str, cwd: Path, system: str, *,
                    mcp_config: Optional[Path], allowed_tools: Optional[list[str]],
                    strict_mcp: bool = True, permission_mode: str = "bypassPermissions",
                    extra_env: Optional[dict[str, str]] = None) -> ClaudeResult:
        """Like :meth:`_run`, but drives a priming turn first (stream-json I/O) so
        the MCP connection registers before the real prompt generates. If a custom
        runner was injected (offline tests), defer to it instead — the priming flow
        only matters against the real CLI. ``extra_env`` is forwarded to the real
        runner (PATH / CLAUDE_PROJECT_DIR for an installed plugin); a fake runner is
        called without it so injected test doubles keep their simple signature.

        ``strict_mcp``/``permission_mode`` default to the prior hardcoded values
        (``True`` / ``bypassPermissions``) so the QA/plugin-real call sites are
        unchanged; the agentic CODE path passes ``permission_mode='acceptEdits'`` so
        the agent can still edit files during a primed coding turn."""
        if self._runner is run_claude:
            return run_claude_primed(
                prompt, cwd=cwd, model=self.model, mcp_config=mcp_config,
                allowed_tools=allowed_tools, append_system_prompt=system,
                strict_mcp=strict_mcp, strip_api_key=True,  # subscription only — never an API key
                permission_mode=permission_mode,
                timeout=self.timeout, runtime=self._runtime, extra_env=extra_env,
            )
        return self._runner(
            prompt, cwd=cwd, model=self.model, mcp_config=mcp_config,
            allowed_tools=allowed_tools, append_system_prompt=system,
            strict_mcp=strict_mcp, strip_api_key=True,
            permission_mode=permission_mode,
            timeout=self.timeout, runtime=self._runtime,
        )

    def _effective_runtime(self) -> ClaudeRuntime:
        """Runtime for writing .mcp.json. Falls back to a native default (offline
        tests have no claude installed) so the config is still produced."""
        return self._runtime or detect() or ClaudeRuntime(
            kind="native", exe="claude", python=sys.executable or "python")

    def _root_dir(self) -> Path:
        """The run-tree root: the injected ``workdir`` or a stable temp dir. Shared by
        per-task dirs (:meth:`_task_dir`) and the per-group plugin-real store
        (:meth:`_plugin_group_store`) so both live under the same tree."""
        import tempfile
        return self._root or Path(tempfile.gettempdir()) / "memeval-claudecode"

    def _task_dir(self, task: Task) -> Path:
        safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in str(task.task_id))[:80]
        return self._root_dir() / self.memory_mode / safe

    def _plugin_group_store(self, task: Task) -> Optional[Path]:
        """The SHARED plugin-real store for ``task``'s group, or ``None`` when the task
        has no ``group_id`` (then plugin-real behaves exactly as before: per-task store
        only, no accumulation).

        Keyed on ``group_id`` under ``<root>/<mode>/_groupstore/<safe(group_id)>`` so a
        SWE-Bench-CL sequence's tasks share one accumulating store while each task keeps
        its own per-task checkout/run dir (different base_commit). Same sanitization as
        :meth:`_task_dir`."""
        if not task.group_id:
            return None
        safe = "".join(
            c if c.isalnum() or c in "._-" else "-" for c in str(task.group_id)
        )[:80]
        return self._root_dir() / self.memory_mode / "_groupstore" / safe


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


def _build_code_agent_prompt(task: Task) -> str:
    """Build the user prompt for the AGENTIC CODE path: the issue text plus the
    repo/base context, with an instruction to EDIT files in the checkout (the
    opposite of the blind path's "output a diff"). Pure / offline-testable."""
    parts = [(task.question or "").strip()]
    if task.repo:
        parts.append(f"Repository: {task.repo}")
    if task.base_commit:
        parts.append(f"Base commit: {task.base_commit}")
    parts.append(
        "You are in a working checkout of this repository. Edit the source files "
        "directly to fix the issue and run the tests to validate. Do NOT output a "
        "diff or paste a patch."
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
