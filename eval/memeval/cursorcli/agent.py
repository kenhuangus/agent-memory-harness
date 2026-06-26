"""CursorCodeAgent — drive the Cursor CLI (`cursor-agent`) over a benchmark task.

A :class:`~memeval.agent.AgentAdapter` (ADR-harness-013), the Cursor sibling of
``ClaudeCodeAgent``. It supports the three core memory modes the cross-harness
comparison needs:

* ``off``         — ask with no memory (baseline). No ``mcp.json`` is written, so the
  run is provably tool-less.
* ``builtin``     — lay prior sessions out as files (``sessions/*.md``) and let the
  agent's native Read/Grep tools find them — Cursor's own file-based memory, the
  analog of Claude Code's builtin mode.
* ``plugin-real`` — wire the SHIPPING cookbook-memory MCP server via ``mcp.json`` in
  an isolated sandbox ``HOME``, pre-clear the MCP approval gate, and let the agent
  call ``recall`` itself. Black box, end to end.

Isolation + auth (ADR-harness-014): each run gets a fresh sandbox ``HOME`` (its own
``~/.cursor/{mcp.json,cli-config.json,auth}``) and authenticates with
``CURSOR_API_KEY`` — keychain-free, so stages can run in parallel.

It reuses the harness-NEUTRAL helpers from ``claudecode.agent`` (pure prompt builders,
diff extraction, session-file layout, plugin-interpreter resolution) — those contain
no Claude wiring — and the shared ``AgentAdapter`` / ``run_agent`` / grader / cost
machinery. It imports NOTHING Claude-specific (no ``run_claude``, no Claude sandbox).
The ``runner`` is injectable (defaults to :func:`cli.run_cursor`) so offline tests
exercise the wiring with a fake CLI.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .. import MEMORY_VERSION
from ..cost import price_for
from ..schema import MemoryItem, RetrievedItem, Task, TaskKind
from . import sandbox as _sandbox
from ..claudecode import checkout as _checkout
from ..claudecode.checkout import GitRunner
from .cli import CursorResult, CursorToolCall, run_cursor
from .platform import CursorRuntime

# Harness-NEUTRAL helpers reused from the Claude adapter (pure, no Claude wiring):
# prompt builders, diff extraction, session-file layout, plugin-interpreter resolver,
# and the events-stream reader. Importing these avoids duplicating ~200 lines of
# tested, harness-agnostic logic (ADR-harness-013 allows reuse of neutral helpers).
from ..claudecode.agent import (
    _build_prompt,
    _build_code_prompt,
    _build_code_agent_prompt,
    _extract_diff,
    _write_session_files,
    _read_events,
    ClaudeCodeAgent as _CCAgent,  # only for its static _plugin_python resolver
)

MemoryMode = str  # "off" | "builtin" | "plugin-real"
_MODES = ("off", "builtin", "plugin-real")

#: Forced-recall retry budget. Like the Claude path, the headless model is not 100%
#: reliable at calling the recall MCP tool on the first try (it sometimes narrates
#: "how do I invoke recall" instead of just calling it). Under the ``forced`` recall
#: policy we retry the turn until a recall event lands in the plugin's events stream,
#: bounded by this. The ``natural`` policy runs exactly once (we don't coerce recall).
_PLUGIN_MAX_TRIES = 3


def _count_recall_events(store_dir: Path) -> int:
    """Number of ``recall`` ops the plugin has logged to ``<store>/events.jsonl`` — the
    signal that the agent actually reached the recall MCP tool (so the turn engaged
    memory). Mirrors the Claude path's events-stream recall count."""
    events = store_dir / "events.jsonl"
    return sum(1 for rec in _read_events(events) if rec.get("op") == "recall")


def _count_daydream_writes(store_dir: Path) -> int:
    """Number of memories the Daydreamer has WRITTEN to ``store_dir`` — the drain
    barrier's "did the write land yet?" signal.

    Counts the durable markdown artifacts the daydream-cli's RouterStore actually
    produces at ``<store>/markdown/memory/<id>.md`` (per ``dreaming.cli._make_store``),
    plus the ``daydream.memory_written`` diary events as a fallback. (NB: this is the
    real write location — the Claude adapter's same-named helper globs
    ``markdown/daydream/`` and so always reads 0, harmlessly falling through to its
    synchronous backstop; we count the correct path so the barrier can actually short-
    circuit when the async hook already wrote.)"""
    md = store_dir / "markdown" / "memory"
    n = len(list(md.glob("*.md"))) if md.is_dir() else 0
    if n:
        return n
    dream = store_dir / "dream"
    if dream.is_dir():
        for dd in dream.glob("*.daydream-events.jsonl"):
            for rec in _read_events(dd):
                name = rec.get("event_type") or rec.get("event") or rec.get("op")
                if name == "daydream.memory_written":
                    n += 1
    return n

# System instructions, folded into the USER prompt (cursor-agent has no
# --append-system-prompt; the system contract must ride in the prompt text).
_SYS_PLAIN = "Answer concisely with just the final answer."
_SYS_BUILTIN_PREFIX = (
    "Earlier conversation history is in files under the sessions/ directory. "
    "Search/read them (grep for keywords from the question) to find what you need, "
    "then answer concisely with just the final answer.\n\n"
)
# plugin-real: the shipping plugin's model-callable tool is `recall`, exposed by an
# MCP server (Cursor lists it as `mcp_plugin-…-cookbook-memory_recall`). The headless
# model otherwise tends to NARRATE ("how do I invoke recall?") instead of just calling
# it, so the forced prefix is imperative: call the MCP recall tool NOW, do not search
# for it, do not use other tools first.
_SYS_PLUGIN_PREFIX = (
    "You have persistent memory exposed as an MCP tool whose name ends in `recall` "
    "(provider `cookbook-memory`). Your FIRST action must be to call that MCP recall "
    "tool with the question as the query — do not search the filesystem, do not look "
    "for how to invoke it, just call the tool. Then answer concisely with just the "
    "final answer using what it returned.\n\n"
)
_SYS_PLUGIN_PREFIX_NATURAL = (
    "Persistent memory is available via the recall tool — use it if prior context "
    "would help, then answer concisely with just the final answer.\n\n"
)
_SYS_CODE = (
    "Respond with ONLY a unified diff (git 'diff --git' format) that fixes the "
    "issue. No prose, no code fences.\n\n"
)
_CODE_AGENT_PREFIX = (
    "Edit the source files in this checkout directly to fix the issue, then run the "
    "tests to confirm. Do NOT output a diff or paste a patch — just make the edits.\n\n"
)
_PLUGIN_CODE_AGENT_PREFIX = (
    "You have persistent memory via the recall tool. First call recall with the "
    "issue text to retrieve prior fixes for this repository, then edit the source "
    "files in this checkout directly to fix the issue and run the tests to confirm. "
    "Do NOT output a diff or paste a patch — just make the edits.\n\n"
)


class CursorCodeAgent:
    """Benchmark agent backed by the Cursor CLI. Satisfies AgentAdapter."""

    def __init__(
        self,
        *,
        model: str = "composer-2.5",
        memory_mode: MemoryMode = "off",
        runner: Optional[Callable[..., CursorResult]] = None,
        runtime: Optional[CursorRuntime] = None,
        workdir: Optional[str | Path] = None,
        k: int = 5,
        timeout: int = 600,
        code_mode: str = "blind",
        project_dir: Optional[str | Path] = None,
        plugin_real_recall_policy: str = "natural",
        git_runner: Optional[GitRunner] = None,
    ) -> None:
        if memory_mode not in _MODES:
            raise ValueError(f"memory_mode must be one of {_MODES}, got {memory_mode!r}")
        if code_mode not in ("blind", "agentic"):
            raise ValueError(f"code_mode must be 'blind' or 'agentic', got {code_mode!r}")
        self.model = model
        self.memory_mode = memory_mode
        self.code_mode = code_mode
        self._runner = runner or run_cursor
        # Injectable git seam for the agentic CODE checkout (mirrors the Claude
        # adapter): defaults to the real subprocess git; offline tests inject a fake
        # that materializes the checkout without network/git.
        self._git_runner = git_runner or _checkout._subprocess_git
        self._runtime = runtime
        self._root = Path(workdir).resolve() if workdir else None
        self._project_dir = Path(project_dir).resolve() if project_dir else None
        self.k = k
        self.timeout = timeout
        self.plugin_real_recall_policy = plugin_real_recall_policy
        self.name = f"cursor-cli:{model}:{memory_mode}"
        price = price_for(model)
        self.price_in = price["in"]
        self.price_out = price["out"]

    # -- AgentAdapter ------------------------------------------------------- #
    def solve(self, task: Task, ctx: Any, **_: Any) -> Any:
        run_dir = self._task_dir(task)
        run_dir.mkdir(parents=True, exist_ok=True)

        if task.kind == TaskKind.CODE:
            if self.code_mode == "agentic":
                return self._solve_code_agentic(task, ctx, run_dir)
            res, _ = self._run(_SYS_CODE + _build_code_prompt(task), run_dir,
                               memory=False, force=False)
            ctx.record_generate(res.text, res.tokens_in, res.tokens_out,
                                model_name=self.model)
            return _extract_diff(res.text)

        prompt = _build_prompt(task)
        if self.memory_mode == "builtin":
            _write_session_files(run_dir, task)
            res, _ = self._run(_SYS_BUILTIN_PREFIX + prompt, run_dir, memory=False,
                               force=False)
        elif self.memory_mode == "plugin-real":
            res = self._solve_plugin_real(task, ctx, prompt, run_dir)
        else:  # off
            res, _ = self._run(_SYS_PLAIN + "\n\n" + prompt, run_dir, memory=False,
                               force=False)

        ctx.record_generate(res.text, res.tokens_in, res.tokens_out, model_name=self.model)
        return res.text

    # -- plugin-real mode (the shipping plugin, black box) ------------------ #
    def _solve_plugin_real(self, task: Task, ctx: Any, prompt: str,
                           run_dir: Path) -> CursorResult:
        """Drive a task against the REAL shipping cookbook-memory plugin, end to end,
        the same way the Claude ``plugin-real`` flow does:

        1. build + load the plugin BUNDLE via ``--plugin-dir`` (the ``recall`` MCP
           server) and write the Daydreamer hooks at user level;
        2. run the recall turn (the model calls ``recall``);
        3. attribute what it recalled to the trajectory;
        4. DRAIN the async daydream WRITE (``sessionEnd`` hook → ``daydream-cli``) so
           the memory it learned lands in the shared substrate before the next task —
           with a synchronous backstop if the async hook didn't finish in time.

        Memory accumulates across tasks because the store directory persists (the
        shared substrate); the harness never copies/seeds/prunes it."""
        store_dir = self._store_dir(task)
        prefix = (_SYS_PLUGIN_PREFIX_NATURAL
                  if self.plugin_real_recall_policy == "natural" else _SYS_PLUGIN_PREFIX)
        before_writes = _count_daydream_writes(store_dir)
        res, sb = self._run_until_recall(prefix + prompt, run_dir, store_dir)
        self._attribute_recall(res, store_dir, ctx, query=getattr(task, "question", "") or "")
        self._drain_daydream(res, sb, store_dir, before_writes)
        return res

    def _run_until_recall(self, prompt: str, cwd: Path, store_dir: Path
                          ) -> tuple[CursorResult, "_sandbox.CursorSandbox"]:
        """Run a plugin-real turn; under the ``forced`` recall policy, retry (bounded by
        :data:`_PLUGIN_MAX_TRIES`) until the plugin logs a new ``recall`` event — the
        headless model isn't 100% reliable at calling the recall tool first try. The
        ``natural`` policy runs once (recall left to the model's discretion). Returns the
        LAST turn's ``(result, sandbox)``. Mirrors the Claude path's retry-until-recall."""
        require_recall = self.plugin_real_recall_policy == "forced"
        tries = _PLUGIN_MAX_TRIES if require_recall else 1
        res: Optional[CursorResult] = None
        sb: Optional["_sandbox.CursorSandbox"] = None
        for _ in range(tries):
            before = _count_recall_events(store_dir)
            res, sb = self._run(prompt, cwd, memory=True, force=True, store_dir=store_dir)
            if not require_recall or _count_recall_events(store_dir) > before:
                break  # recall engaged (or natural policy: one shot)
        return res, sb  # type: ignore[return-value]

    # -- agentic CODE mode (real checkout; harness grades) ------------------ #
    def _solve_code_agentic(self, task: Task, ctx: Any, run_dir: Path) -> Any:
        from ..agent import AgentResult
        from ..claudecode.checkout import (
            CheckoutError, capture_diff, checkout_with_cache,
        )

        checkout = run_dir / "repo"
        try:
            checkout_with_cache(task.repo or "", task.base_commit, checkout,
                                git_runner=self._git_runner, timeout=self.timeout)
        except CheckoutError as exc:
            ctx.note(f"checkout failed: {str(exc)[:200]}")
            return AgentResult(prediction="", patch="", success=None)

        base_prompt = _build_code_agent_prompt(task)
        if self.memory_mode == "builtin":
            _write_session_files(checkout, task)
            res, _ = self._run(_CODE_AGENT_PREFIX + base_prompt, checkout, memory=False,
                               force=True)
        elif self.memory_mode == "plugin-real":
            store_dir = self._store_dir(task)
            before_writes = _count_daydream_writes(store_dir)
            res, sb = self._run(_PLUGIN_CODE_AGENT_PREFIX + base_prompt, checkout,
                                memory=True, force=True, store_dir=store_dir)
            self._attribute_recall(res, store_dir, ctx, query=task.question or "")
            self._drain_daydream(res, sb, store_dir, before_writes)
        else:  # off
            res, _ = self._run(_CODE_AGENT_PREFIX + base_prompt, checkout, memory=False,
                               force=True)

        diff = capture_diff(checkout, base_commit=task.base_commit,
                            git_runner=self._git_runner)
        ctx.record_generate(res.text, res.tokens_in, res.tokens_out, model_name=self.model)
        return AgentResult(prediction=diff, patch=diff, success=None)

    # -- recall attribution ------------------------------------------------- #
    def _attribute_recall(self, res: CursorResult, store_dir: Path, ctx: Any,
                          *, query: str) -> None:
        """Record the agent's recall(s) to the trajectory.

        Two evidence sources, preferred in order: (1) the plugin's OWN events stream
        (``events.jsonl``, enriched ``meta.hits`` per ADR-harness-007) — the richest,
        identical to the Claude path; (2) the stream-json ``tool_call`` events as a
        fallback when the events stream is empty (records the recall query so
        recall_attempted is honest even if hit metadata is unavailable)."""
        events = store_dir / "events.jsonl"
        recorded = False
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
            ctx.record_retrieve(hits, query=rec.get("query", query))
            recorded = True
        if recorded:
            return
        # Fallback: attribute from the stream's tool_call events (query only).
        for tc in res.tool_calls:
            if tc.tool == _sandbox.RECALL_TOOL or tc.server == _sandbox.MCP_SERVER_NAME:
                q = str(tc.args.get("query") or query)
                ctx.record_retrieve([], query=q)

    # -- run helper --------------------------------------------------------- #
    def _run(self, prompt: str, cwd: Path, *, memory: bool, force: bool,
             store_dir: Optional[Path] = None) -> tuple[CursorResult, "_sandbox.CursorSandbox"]:
        """Build a HOME sandbox (isolation + keychain + API key), and for a memory turn
        build the shipping plugin BUNDLE (the ``recall`` MCP server, loaded via
        ``--plugin-dir``) + write the Daydreamer hooks at user level. Drive one
        ``cursor-agent`` turn. Returns ``(result, sandbox)`` so the caller can drain the
        daydream write.

        ``memory=False`` builds NO bundle and writes NO hooks → the run is provably
        tool-less (the baseline/builtin guarantee). ``memory=True`` is the faithful
        ``plugin-real`` setup (ADR-harness-013): bundle + hooks + ``--plugin-dir`` +
        ``--approve-mcps``."""
        sb = _sandbox.build(self._sandbox_dir(cwd))
        plugin_dir: Optional[Path] = None
        extra_env = dict(self._dream_env())
        if memory:
            resolved_store = store_dir or self._store_root()
            _sandbox.setup_real_plugin(
                sb, plugin_bin_dir=self._plugin_bin_dir(), store_dir=resolved_store)
            plugin_dir = sb.plugin_dir
            # CRITICAL: the user-level sessionEnd hook (the daydream WRITE trigger)
            # inherits its env from this cursor-agent process. Without MEMORY_STORE here
            # the hook's daydream-cli writes to the wrong place and memory never
            # accumulates. Set it so the async hook AND the MCP server resolve the same
            # per-run substrate.
            extra_env["MEMORY_STORE"] = str(resolved_store)
        env = _sandbox.env_for(sb, extra_env=extra_env)
        res = self._runner(
            prompt, cwd=cwd, model=self.model, approve_mcps=memory, force=force,
            trust=True, workspace=cwd, plugin_dir=plugin_dir, timeout=self.timeout,
            runtime=self._runtime, env=env,
        )
        return res, sb

    # -- daydream drain barrier (mirror of ClaudeCodeAgent._drain_daydream) -- #
    def _drain_daydream(self, res: CursorResult, sb: "_sandbox.CursorSandbox",
                        store_dir: Path, before_writes: int) -> None:
        """Block until the plugin's async ``sessionEnd`` daydream WRITE has landed, so
        the next task doesn't race it on the shared store — with a synchronous backstop
        that drives ``daydream-cli`` on the run's transcript if the async hook didn't
        finish (or didn't survive process exit). Mirrors the Claude path's barrier;
        copies nothing (the plugin owns the store).

        No-op under the offline fake runner (no real daydream / transcript)."""
        if self._runner is not run_cursor:
            return
        deadline = time.monotonic() + self._drain_timeout_secs()
        while time.monotonic() < deadline:
            if _count_daydream_writes(store_dir) > before_writes:
                return  # the async sessionEnd hook's write landed — drained.
            time.sleep(0.25)
        # Backstop: drive the daydreamer synchronously on this turn's transcript.
        sid, transcript = self._discover_transcript(res, sb)
        if not sid or transcript is None:
            return  # transcript not found — poll-only (do not fabricate a write)
        self._run_daydream_cli(sid, transcript, store_dir)

    def _discover_transcript(self, res: CursorResult,
                             sb: "_sandbox.CursorSandbox") -> tuple[Optional[str], Optional[Path]]:
        """Best-effort (session_id, transcript_path) for the just-completed turn.

        Cursor writes the transcript under ``<CURSOR_DATA_DIR>/projects/<slug>/
        agent-transcripts/<sid>/<sid>.jsonl``; the result's ``session_id`` is the
        ``<sid>``. We glob for ``<sid>.jsonl`` under the sandbox transcripts root (robust
        to the slug encoding), newest match wins."""
        raw = res.raw if isinstance(res.raw, dict) else {}
        sid = raw.get("session_id") or raw.get("sessionId")
        if not sid:
            return None, None
        root = sb.transcripts_root
        if not root.is_dir():
            return str(sid), None
        matches = list(root.glob(f"**/{sid}.jsonl"))
        if not matches:
            return str(sid), None
        return str(sid), max(matches, key=lambda p: p.stat().st_mtime)

    def _run_daydream_cli(self, session_id: str, transcript: Path,
                          store_dir: Path) -> None:
        """Synchronous daydream backstop: normalize the Cursor transcript and drive
        ``daydream-cli daydream`` — the SAME engine + the SAME normalizer the
        ``sessionEnd`` hook uses (``cookbook_memory.adapters.cursor.hooks_handler``),
        so the write completes before the next task. Fail-open."""
        import subprocess

        from cookbook_memory.adapters.cursor.hooks_handler import _normalize_transcript

        bin_dir = self._plugin_bin_dir()
        py = _sandbox._bundle_python(bin_dir)
        env = {**os.environ, **self._dream_env(), "MEMORY_STORE": str(store_dir)}
        with tempfile.TemporaryDirectory(prefix="cbmem-cursor-drain-") as td:
            norm = _normalize_transcript(transcript, Path(td))
            log_arg = str(norm) if norm is not None else str(transcript)
            try:
                subprocess.run(
                    [py, "-m", "memeval.dreaming.cli", "daydream",
                     "--log", log_arg, "--session", session_id, "--store", str(store_dir)],
                    env=env, capture_output=True, text=True, timeout=self.timeout,
                    check=False,
                )
            except Exception:
                return  # fail-open: a backstop failure must not crash the run.

    @staticmethod
    def _drain_timeout_secs() -> float:
        raw = os.environ.get("MEMEVAL_DAYDREAM_DRAIN_SECS")
        if raw:
            try:
                return max(0.0, float(raw))
            except ValueError:
                pass
        return 8.0

    def _dream_env(self) -> dict[str, str]:
        """OPENROUTER_API_KEY + DREAM_* passed through to the MCP server + daydream
        write path (mirrors the Claude adapter's _add_dream_env)."""
        out: dict[str, str] = {}
        ork = os.environ.get("OPENROUTER_API_KEY")
        if ork:
            out["OPENROUTER_API_KEY"] = ork
        for k, v in os.environ.items():
            if k.startswith("DREAM_"):
                out[k] = v
        return out

    # -- paths / resolution ------------------------------------------------- #
    def _plugin_bin_dir(self) -> Optional[str]:
        """The venv ``bin`` dir whose interpreter can import ``cookbook_memory`` (and
        the MCP SDK) — used to pin the bundle's MCP command and the daydream hook's
        interpreter to the RIGHT environment.

        This is load-bearing: cursor-agent launches the bundle's MCP server with this
        command, and if it points at an interpreter WITHOUT ``cookbook_memory`` (e.g. a
        bare host ``python3``), the ``recall`` tool never registers and the model
        silently can't recall (verified failure mode). Resolution order:

        1. the ``memory-cli`` console script's dir (a real install on PATH), but only
           if its sibling interpreter can actually import ``cookbook_memory``;
        2. otherwise the CURRENT interpreter (``sys.executable``) — the harness runs
           under the venv that has the plugin installed, so this is the reliable
           default (``memory-cli`` is often not on PATH under the eval venv)."""
        import sys

        candidates: list[Optional[str]] = []
        py = _CCAgent._plugin_python(os.environ.get("PATH"))
        if py:
            candidates.append(py)
        candidates.append(sys.executable)
        for cand in candidates:
            if cand and self._can_import_plugin(cand):
                parent = Path(cand).parent
                if parent.is_dir():
                    return str(parent)
        return None

    @staticmethod
    def _can_import_plugin(python: str) -> bool:
        """True iff ``python`` can import ``cookbook_memory`` + the MCP SDK — i.e. it can
        actually run the bundle's ``-m cookbook_memory mcp`` server."""
        import subprocess
        try:
            r = subprocess.run([python, "-c", "import cookbook_memory, mcp"],
                               capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            return False
        return r.returncode == 0

    def _root_dir(self) -> Path:
        base = self._root or Path(tempfile.gettempdir()) / "memeval-cursorcli"
        return base / f"v{MEMORY_VERSION}"

    def _task_dir(self, task: Task) -> Path:
        safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in str(task.task_id))[:80]
        return self._root_dir() / self.memory_mode / safe

    def _sandbox_dir(self, cwd: Path) -> Path:
        """A fresh per-turn sandbox HOME, sibling to the run dir (independent of cwd so
        a bare HOME never collides with the working checkout)."""
        return Path(cwd).parent / f".cursor-home-{Path(cwd).name}"

    def _store_root(self) -> Path:
        return self._project_dir or self._root_dir() / "_memory"

    def _store_dir(self, task: Task) -> Path:
        """The plugin's MEMORY_STORE for this task. Uses the shared substrate
        (``project_dir``, set by the pipeline for accumulation) when present, else a
        per-task fresh store under the run tree."""
        if self._project_dir is not None:
            store = self._project_dir / ".cookbook-memory"
        else:
            store = self._task_dir(task) / ".cookbook-memory"
        store.mkdir(parents=True, exist_ok=True)
        return store


__all__ = ["CursorCodeAgent", "MemoryMode"]
