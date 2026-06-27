"""grok_runner — drive the xAI Grok CLI (grok.exe / grok) as the agent-under-test
on SWE-Bench-CL CODE tasks, the SAME way the claude `base` (no-memory) arm runs.

Why this exists
---------------
``eval/memeval/claudecode/agent.py`` hardwires the ``claude`` CLI as the solver.
To compare *grok-as-coder* vs *claude-as-coder* we need the identical task →
prediction → grade pipeline but with grok in the solver seat. grok has no
harness-wired memory mechanism (no CLAUDE.md autoload, no cookbook plugin Stop
hook), so the ONLY fair arm is ``base`` / no-memory — exactly mode=off.

Contract (mirrors ClaudeCodeAgent's agentic base path)
------------------------------------------------------
1. ``prepare_checkout(repo, base_commit)``  — real working tree (reused).
2. drive ``grok`` as a coding agent IN that checkout (it reads/edits files,
   ``--permission-mode acceptEdits``), no memory, no web.
3. ``capture_diff(checkout)`` — ``git diff`` is the prediction (reused).
4. ``SwebenchHostGrader`` grades it — SAME grader as the claude run (reused).

This is the agentic counterpart of the claude base arm (which also edits a
checkout and captures git diff). Everything except the solver subprocess is the
unchanged harness machinery, so the only variable is claude-vs-grok.

ponytail: one function (`solve_task`) + a thin grok subprocess call. No class,
no AgentAdapter plumbing — the pipeline isn't involved; the driver script loops.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

# Same coding-agent instruction the claude agentic base arm uses (agent.py
# _SYS_CODE_AGENT + _CODE_AGENT_PREFIX), so the two agents get the same task.
_PROMPT = (
    "You are a software engineer working in a real checkout of this repository. "
    "Edit the source files in this checkout directly to fix the issue described "
    "below, then run the project's tests to confirm. Do NOT output a diff or "
    "paste a patch — just make the edits to the files.\n\n"
    "Issue:\n{issue}\n"
)


def build_grok_prompt(question: str) -> str:
    return _PROMPT.format(issue=(question or "").strip())


def run_grok(prompt: str, cwd: Path, *, timeout: int = 1800,
             grok_exe: str = "grok") -> tuple[int, str, str]:
    """Drive grok headlessly in ``cwd`` as a coding agent. Returns (rc, stdout, stderr).

    --yolo auto-approves tool executions so grok actually EDITS files; no web, no
    memory (the fair base arm). The prompt goes on argv via -p (same as `claude -p`).
    Never raises — a timeout or crash returns a non-zero rc so the caller still
    captures whatever diff exists.

    Why --yolo and not --permission-mode acceptEdits: in headless -p, acceptEdits
    leaves the edit tool awaiting approval, so grok narrates "I'll edit…" and the
    turn ends with ZERO file writes (diff_len=0 — the original show-stopper). --yolo
    is grok's documented "auto-approve all tool executions" and is what actually
    lets the edits land. Verified: trivial edit task -> git diff HEAD = 255 bytes.
    """
    argv = [
        grok_exe, "-p", prompt,
        "--cwd", str(cwd),
        "--yolo",
        "--disable-web-search",
        "--no-memory",
        "--max-turns", "40",
    ]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, check=False)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        return 124, (exc.stdout or "" if isinstance(exc.stdout, str) else ""), "TimeoutExpired"
    except Exception as exc:  # noqa: BLE001 - never crash the run
        return 1, "", f"{type(exc).__name__}: {exc}"


def capture_tracked_diff(checkout: Path) -> str:
    """Diff of TRACKED-file changes only (the real SWE-bench prediction).

    Why not reuse ``checkout.capture_diff``: that one does ``git add -- .`` which
    stages EVERY untracked file. grok runs the project's tests by creating a
    ``.venv/`` and leaves ``__pycache__``/``*.pyc`` build artifacts inside the
    checkout, so ``add -.`` produced a 13–39 MB diff of venv noise that never
    applies (base drift). The gold SWE-bench patch only ever modifies files that
    already exist in the repo, so ``git diff HEAD`` over tracked files is exactly
    the prediction and excludes all of grok's untracked junk. Empty / any error →
    "" (an honest empty patch), matching capture_diff's contract.
    """
    import subprocess
    try:
        r = subprocess.run(["git", "diff", "HEAD"], cwd=str(checkout),
                           capture_output=True, text=True, timeout=120, check=False)
        return r.stdout or "" if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001 - unreadable diff is "no change", not an error
        return ""


def solve_task(task, checkout_root: Path, *, grader, timeout: int = 1800,
               grok_exe: str = "grok", prompt: Optional[str] = None) -> dict:
    """Run ONE CODE task end-to-end with grok and grade it. Returns a result dict.

    checkout → grok edits → capture TRACKED diff → grade. ``prompt`` overrides the
    base prompt (the builtin/plugin arms prepend memory instructions/context). The
    grader is injected (SwebenchHostGrader); the diff excludes grok's untracked
    venv/build artifacts (see capture_tracked_diff)."""
    from .checkout import CheckoutError, prepare_checkout

    checkout = Path(checkout_root) / "repo"
    out: dict = {"task_id": task.task_id, "repo": task.repo,
                 "base_commit": task.base_commit}
    try:
        prepare_checkout(task.repo or "", task.base_commit, checkout, timeout=timeout)
    except CheckoutError as exc:
        out.update(error=f"checkout failed: {exc}", resolved=None, diff_len=0)
        return out

    full_prompt = prompt if prompt is not None else build_grok_prompt(task.question)
    rc, stdout, stderr = run_grok(full_prompt, checkout, timeout=timeout,
                                  grok_exe=grok_exe)
    out["grok_rc"] = rc
    out["checkout"] = str(checkout)
    out["grok_stdout_tail"] = (stdout or "")[-2000:]
    diff = capture_tracked_diff(checkout)
    out["diff_len"] = len(diff)
    out["diff"] = diff
    verdict: Optional[bool] = grader(task, diff)
    out["resolved"] = verdict
    out["ungraded_reason"] = getattr(grader, "last_reason", None) if verdict is None else None
    return out


# ---------------------------------------------------------------------------- #
# builtin arm — the harness's file-based memory, driven for grok.
# ---------------------------------------------------------------------------- #
# Same idea as ClaudeCodeAgent builtin: lay prior sessions out as sessions/*.md in
# the checkout, instruct the agent to grep/read them before solving. grok has Read/
# Grep/Glob tools, so it can do the native retrieval Claude does. Recall is
# attributed by checking grok's OWN transcript (chat_history.jsonl) for a read of a
# session file — the honest analogue of _attribute_builtin_recall.

_BUILTIN_PREFIX = (
    "Earlier solved issues for this repository are stored as Markdown files under "
    "the sessions/ directory (one per prior issue, each with its problem and the "
    "[solution] patch). BEFORE editing, search/read those files (grep for keywords "
    "from the issue) — a similar past fix may guide this one. Then "
)


def build_builtin_prompt(question: str) -> str:
    base = build_grok_prompt(question)
    # Splice the builtin instruction in front of the coding directive.
    return _BUILTIN_PREFIX + base[0].lower() + base[1:]


def write_session_files(checkout: Path, task) -> int:
    """Lay the task's prior sessions out as sessions/*.md in the checkout (reuses the
    harness's exact layout via agent._write_session_files). Returns the count."""
    from .agent import _write_session_files
    _write_session_files(checkout, task)
    return len(getattr(task, "sessions", None) or [])


def _grok_transcript(checkout: Path) -> Optional[Path]:
    """Locate grok's chat_history.jsonl for a run whose cwd was ``checkout``.

    grok stores sessions under ~/.grok/sessions/<urlencoded-cwd>/<sessionId>/. Pick
    the most-recent chat_history.jsonl under the encoded checkout path."""
    import urllib.parse
    enc = urllib.parse.quote(str(checkout), safe="")
    root = Path.home() / ".grok" / "sessions" / enc
    if not root.is_dir():
        return None
    cands = list(root.glob("*/chat_history.jsonl"))
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)


def builtin_recall_hits(checkout: Path, task) -> list[str]:
    """Session ids whose file grok actually read/grepped, from its transcript.

    Honest attribution (mirrors _attribute_builtin_recall): a recall counts ONLY
    when grok's transcript names a sessions/*.md file we wrote. Returns the matched
    session ids ([] if none / no transcript)."""
    tp = _grok_transcript(checkout)
    if tp is None:
        return []
    try:
        text = tp.read_text(errors="replace")
    except OSError:
        return []
    hits: list[str] = []
    for i, s in enumerate(getattr(task, "sessions", None) or []):
        safe_id = "".join(c if (c.isalnum() or c in "-_") else "_"
                          for c in str(s.session_id))[:60]
        fname = f"session_{i:04d}_{safe_id}.md"
        if fname in text:
            hits.append(str(s.session_id))
    return hits


# ---------------------------------------------------------------------------- #
# plugin arm — the cookbook store, orchestrated externally (grok has no Stop hook).
# Recall: store.search() injected into the prompt BEFORE the turn.
# Write: the dreaming CLI runs daydream over grok's transcript AFTER the turn.
# Both use the plugin's OWN surfaces; the store accumulates across the sequence.
# ---------------------------------------------------------------------------- #

def plugin_recall(store_dir: Path, query: str, k: int = 5) -> list[str]:
    """Top-k memory contents from the cookbook store for ``query`` (plugin's own
    RouterStore.search — the same recall surface the plugin exposes)."""
    from cookbook_memory.core.contract import build_store
    try:
        hits = build_store(str(store_dir)).search(query or "", k=k)
    except Exception:  # noqa: BLE001 - empty/missing store -> no memories
        return []
    out: list[str] = []
    for h in hits:
        it = getattr(h, "item", h)
        c = (getattr(it, "content", "") or "").strip()
        if c:
            out.append(c)
    return out


def store_count(store_dir: Path) -> int:
    from cookbook_memory.core.contract import build_store
    try:
        return len(list(build_store(str(store_dir)).all()))
    except Exception:  # noqa: BLE001
        return 0


def build_plugin_prompt(question: str, memories: list[str]) -> str:
    base = build_grok_prompt(question)
    if not memories:
        return base
    blob = "\n\n".join(f"[memory {i+1}] {m}" for i, m in enumerate(memories))
    return (
        "Relevant lessons recalled from prior issues in this repository "
        "(use them if applicable):\n" + blob + "\n\n" + base
    )


def daydream_write(store_dir: Path, session_id: str, transcript_text: str,
                   *, python_exe: str = "python") -> int:
    """Run the plugin's daydream extraction over ``transcript_text`` to WRITE
    memories into ``store_dir`` (the cookbook store). Returns net memories added.

    Drives ``memeval.dreaming.cli daydream`` exactly as the Claude Stop hook would,
    feeding grok's transcript as the --log. Needs OPENROUTER_API_KEY in env."""
    import subprocess
    import tempfile
    before = store_count(store_dir)
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(transcript_text)
        log_path = fh.name
    env = {**os.environ, "MEMORY_STORE": str(store_dir),
           "DREAM_NOISE_FILTER": os.environ.get("DREAM_NOISE_FILTER", "0")}
    try:
        subprocess.run(
            [python_exe, "-m", "memeval.dreaming.cli", "daydream",
             "--session", session_id, "--log", log_path, "--store", str(store_dir)],
            env=env, capture_output=True, text=True, timeout=600, check=False)
    except Exception:  # noqa: BLE001 - fail-open: a write failure must not crash the run
        pass
    finally:
        try:
            Path(log_path).unlink()
        except OSError:
            pass
    return store_count(store_dir) - before


__all__ = [
    "build_grok_prompt", "run_grok", "solve_task", "capture_tracked_diff",
    "build_builtin_prompt", "write_session_files", "builtin_recall_hits",
    "plugin_recall", "store_count", "build_plugin_prompt", "daydream_write",
]
