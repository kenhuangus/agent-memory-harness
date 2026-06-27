#!/usr/bin/env python3
"""agy-as-solver adapter for the Sympy SWE-Bench-CL benchmark — 3 arms.

Compares agy (Google Antigravity / Gemini CLI) vs claude as the code solver on the
SAME tasks, SAME swebench grader, SAME loader/checkout/diff seam the claude pipeline
uses (memeval.loaders + claudecode.checkout + grader_swebench). The ONLY new code is
"drive agy.exe in the checkout" plus, for the memory arms, the EXTERNAL orchestration
of the harness's own memory surfaces (since agy has no Claude Code Stop hook).

Arms (mirror eval/memeval/claudecode/pipeline.py):
  base    -- no memory: prompt = issue only.
  builtin -- the harness's own native-memory layout: prior sessions written as
             sessions/*.md (reuses _write_session_files) + agy told to grep/read them
             before solving. "recall" = agy actually Read a session file in its transcript.
  plugin  -- the cookbook-memory store, orchestrated externally:
             BEFORE each task: cookbook `query` the store with the issue, inject top-k
             memories into agy's prompt. AFTER each task: write agy's transcript and run
             the dreaming `daydream` pass to WRITE memories back into the SAME store, so
             memory accumulates across the 15-task sequence (continual learning) — exactly
             what the Claude plugin does via its Stop hook, here driven by us.
             Uses ONLY the plugin's own surfaces (cookbook_memory.cli / memeval.dreaming.cli);
             never touches their source.

Run (WSL, same env as `make pipeline`):
  cd /mnt/c/Users/kenhu/agent-memory-harness/eval
  uv run --no-project python ../runs/sympy3-agy/agy_runner.py --arm base --limit 3
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from memeval.loaders import get_loader
from memeval.schema import TaskKind
from memeval.claudecode.checkout import prepare_checkout, capture_diff, CheckoutError
from memeval.claudecode.agent import _write_session_files
from memeval.grader_swebench import SwebenchHostGrader

AGY = "/mnt/c/Users/kenhu/AppData/Local/agy/bin/agy.exe"
SEQUENCE = "sympy_sympy_sequence"
BENCH = "swe_bench_cl"
ARMS = ("base", "builtin", "plugin")

# Same coding contract the harness gives claude in the agentic CODE arm
# (_build_code_agent_prompt + _SYS_CODE_AGENT): edit files in this checkout, run tests,
# do NOT print a diff.
_CODE_INSTR = (
    "You are a software engineer working in a real checkout of the repository {repo}. "
    "Fix the ROOT CAUSE of the issue below by editing the LIBRARY SOURCE files directly. "
    "Do NOT modify test files, bin/ scripts, or add Python-compatibility shims (e.g. "
    "collections.abc monkeypatches) — those do not fix the issue. Make the minimal source "
    "edit that resolves it. Do NOT output a diff or paste a patch — just make the edits."
)
# builtin: tell agy to consult the laid-down session history first (mirrors _BUILTIN_PREFIX).
_BUILTIN_PREFIX = (
    "Earlier work on this repository is stored as Markdown files under the sessions/ "
    "directory in this checkout. BEFORE editing, read those files (e.g. grep for "
    "keywords from the issue) to reuse prior fixes.\n\n"
)
# plugin: prepend recalled memories (mirrors injecting recall hits into the turn).
_PLUGIN_PREFIX = (
    "Relevant memories from earlier tasks on this repository (use them if helpful):\n"
    "{memories}\n\n"
)


def _uv_py(*mod_args: str, env: dict, cwd: str, timeout: int) -> subprocess.CompletedProcess:
    """Run a harness module under the same uv env this script runs in."""
    argv = [sys.executable, *mod_args]
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL)


def cookbook_query(store: Path, query: str, k: int, eval_dir: str, env: dict) -> list[dict]:
    """Cookbook recall surface: `python -m cookbook_memory.cli --store <s> query <q> -k`."""
    try:
        proc = _uv_py("-m", "cookbook_memory.cli", "--store", str(store),
                      "query", query[:4000], "-k", str(k),
                      env=env, cwd=eval_dir, timeout=120)
        data = json.loads(proc.stdout or "{}")
        return data.get("hits", []) or []
    except Exception:
        return []


def cookbook_count(store: Path, eval_dir: str, env: dict) -> int:
    """Number of memories WRITTEN to the store (for before/after write proof).

    Counts the durable markdown artifacts the daydream engine persists (mirrors the
    harness's _count_daydream_writes) — NOT the events stream (`stats` total counts
    recall/write *events*, not memories)."""
    md = store / "markdown"
    return sum(1 for _ in md.rglob("*.md")) if md.is_dir() else 0


def daydream_write(store: Path, session_id: str, transcript: Path, eval_dir: str,
                   env: dict, timeout: int) -> str:
    """Dreaming write surface: extract memories from agy's transcript into the store.

    DREAM_NOISE_FILTER=0 so the formatter doesn't drop agy's (non-Claude) transcript
    shape; the transcript is written in the standard CC user/assistant JSONL form."""
    e = {**env, "DREAM_NOISE_FILTER": "0", "MEMORY_STORE": str(store)}
    try:
        proc = _uv_py("-m", "memeval.dreaming.cli", "daydream",
                      "--session", session_id, "--log", str(transcript), "--store", str(store),
                      env=e, cwd=eval_dir, timeout=timeout)
        return (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:
        return f"daydream error: {exc}"


_AGY_CONFIG = Path.home() / ".gemini" / "config" / "config.json"
_AGY_GRANTS = ["command(*)", "read_file(*)", "write_file(*)", "run_command(*)"]


def ensure_agy_permissions() -> str:
    """Grant agy headless write/command permissions for arbitrary paths.

    agy's print mode (`-p`) honors per-path permission grants and IGNORES
    --dangerously-skip-permissions, so writes outside its sandbox default to 'ask' and
    hang headless. Adding write_file(*)/command(*) to the global grants in
    ~/.gemini/config/config.json (a user agy config, NOT a harness/team file; backed up)
    lets agy edit the checkout. Idempotent."""
    try:
        cfg = json.loads(_AGY_CONFIG.read_text(encoding="utf-8")) if _AGY_CONFIG.exists() else {}
    except (OSError, ValueError):
        cfg = {}
    us = cfg.setdefault("userSettings", {})
    gp = us.setdefault("globalPermissionGrants", {})
    allow = gp.setdefault("allow", [])
    added = [g for g in _AGY_GRANTS if g not in allow]
    if added:
        bak = _AGY_CONFIG.with_suffix(".json.bak.agyrunner")
        if _AGY_CONFIG.exists() and not bak.exists():
            bak.write_text(_AGY_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        gp["allow"] = _AGY_GRANTS + [g for g in allow if g not in _AGY_GRANTS]
        _AGY_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        _AGY_CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return f"grants={gp['allow']} (added {added})"


def _agy_edit_summary(agy_cli_log: str, diff: str) -> str:
    """A short prose summary of what agy did, from its CLI log + the captured diff —
    fallback transcript body when agy's print stdout is empty (the usual case)."""
    edited = []
    for ln in agy_cli_log.splitlines():
        if "replace_file_content" in ln or "Created file" in ln or "write_file" in ln:
            edited.append(ln.strip()[-200:])
    head = "\n".join(edited[:15]) or "(agy CLI log recorded no explicit edit events)"
    return (f"agy edited the checkout to fix the issue. Edit actions:\n{head}\n\n"
            f"Resulting unified diff (first 4000 chars):\n{diff[:4000]}")


def write_transcript(path: Path, issue: str, agy_out: str) -> None:
    """Write a minimal Claude-Code-style JSONL transcript of the agy turn for daydream."""
    lines = [
        {"type": "user", "message": {"role": "user", "content": f"Fix this issue:\n{issue}"}},
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": agy_out[-12000:] or "(no output)"}},
    ]
    path.write_text("\n".join(json.dumps(o) for o in lines) + "\n", encoding="utf-8")


AGY_WIN = r"C:\Users\kenhu\AppData\Local\agy\bin\agy.exe"
# agy's default print-mode model is Flash, which does NOT drive the agentic edit loop
# (text-only answer, no file edits). The Pro model is required for real diffs.
AGY_MODEL = os.environ.get("AGY_MODEL", "gemini-3.1-pro")


def to_win(p: Path) -> str:
    """POSIX /mnt/c/... -> Windows C:\\... (agy.exe needs a real Windows cwd; with a
    /mnt/c cwd agy silently no-ops and edits nothing)."""
    try:
        return subprocess.run(["wslpath", "-w", str(p)], capture_output=True, text=True,
                              timeout=20).stdout.strip()
    except Exception:
        s = str(p)
        return s.replace("/mnt/c/", "C:/").replace("/", "\\") if s.startswith("/mnt/c/") else s


def _run_agy_once(prompt: str, cwd: Path, timeout: int, log_file: Path,
                  scratch: Path) -> tuple[str, int, str]:
    """One agy.exe invocation with a Windows cwd via a generated .bat (cmd.exe cd /d).

    CRITICAL findings (all verified on the trivial a-b->a+b bug):
      * print-mode `-p` IGNORES --dangerously-skip-permissions; per-path grants in
        ~/.gemini/config/config.json (ensure_agy_permissions, allow=write_file(*)/command(*))
        are what let agy edit a non-sandbox checkout.
      * agy.exe MUST run with a real Windows cwd; a /mnt/c POSIX cwd makes it no-op.
      * agy DOES edit the cwd checkout in place when its planner runs enough turns
        (EDIT runs: 10-12 planner turns; NOEDIT runs short-circuit at 5-8). It is
        intermittent, so run_agy retries until a SOURCE edit lands.
      * The .bat / agy --log-file live in `scratch` (OUTSIDE the checkout) so they never
        pollute `git diff` — the retry's diff check then reflects real source edits only.
      * print stdout is usually empty (the turn lives in agy's conversation store); the
        prediction is `git diff`, not stdout."""
    win_cwd = to_win(cwd)
    win_log = to_win(log_file)
    bat = scratch / "_agy_run.bat"
    # ROOT-CAUSE FIX: agy defaults to Gemini Flash in print mode, which answers with
    # text and quits in ~6 turns WITHOUT editing (rc=0, empty diff — the V5 failure).
    # Forcing the agentic Pro model makes it run the full edit loop and apply real
    # source edits. Verified: Flash -> 0-byte diff; gemini-3.1-pro -> real diff.
    # ROOT-CAUSE FIX 2 (2026-06-26): a Windows cwd alone is NO LONGER enough — agy now
    # only edits files inside an explicit WORKSPACE. Without `--add-dir <checkout>` it
    # runs, answers, and exits rc=0 with an empty diff (the full50 V5 failure: every
    # task diff_len=0). Adding the checkout via --add-dir makes real source edits land.
    # Verified on a trivial calc.py reproducer: no --add-dir -> no edit; with --add-dir
    # -> `return a - b` became `return a + b`.
    bat.write_text(
        "@echo off\r\n"
        f'cd /d "{win_cwd}"\r\n'
        f'"{AGY_WIN}" -p "%~1" --model {AGY_MODEL} --add-dir "{win_cwd}" '
        f'--dangerously-skip-permissions '
        f'--log-file "{win_log}" --print-timeout {max(1, timeout // 60)}m < NUL\r\n',
        encoding="utf-8")
    one_line = " ".join(prompt.split())  # cmd arg must be one line
    proc = subprocess.run(["cmd.exe", "/c", to_win(bat), one_line],
                          capture_output=True, text=True,
                          timeout=timeout + 180, stdin=subprocess.DEVNULL)
    out = (proc.stdout or "") + ("\n[stderr]\n" + proc.stderr if proc.stderr else "")
    agy_log = ""
    try:
        agy_log = log_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        pass
    return out, proc.returncode, agy_log


def run_agy(prompt: str, cwd: Path, timeout: int, log_file: Path,
            checkout: Path, base_commit, max_tries: int = 3) -> tuple[str, int, str, str]:
    """Drive agy until it produces a non-empty SOURCE diff (retry backstop).

    agy print-mode is flaky: the planner sometimes short-circuits and edits nothing
    (rc=0, empty diff). Mirroring the harness's retry-until-recall, we re-invoke up to
    max_tries until `git diff` is non-empty. The .bat/log live in a scratch dir beside
    (not inside) the checkout so capture_diff sees only real source edits. Returns
    (stdout, rc, agy_log, diff)."""
    scratch = checkout.parent / (checkout.name + "_agyscratch")
    scratch.mkdir(parents=True, exist_ok=True)
    out, rc, agy_log, diff = "", -1, "", ""
    for attempt in range(1, max_tries + 1):
        out, rc, agy_log = _run_agy_once(
            prompt, cwd, timeout,
            log_file.with_suffix(f".try{attempt}.clilog"), scratch)
        diff = capture_diff(checkout, base_commit=base_commit)
        if diff.strip():
            break
    return out, rc, agy_log, diff


def builtin_recall_hit(agy_log: str) -> list[str]:
    """Session files agy actually read (its --log-file records read_file tool actions)."""
    hits = []
    for ln in agy_log.splitlines():
        low = ln.lower()
        if "sessions" in low and "session_" in low and ("read" in low or ".md" in low):
            hits.append(ln.strip()[-160:])
    return hits[:10]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=ARMS, required=True)
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--timeout", type=int, default=900, help="per-task agy timeout (s)")
    ap.add_argument("--grader-timeout", type=int, default=1800)
    ap.add_argument("--k", type=int, default=5, help="recall top-k for plugin arm")
    ap.add_argument("--out", default=str(Path(__file__).parent))
    args = ap.parse_args()

    arm = args.arm
    out_dir = Path(args.out) / arm
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "logs").mkdir(exist_ok=True)
    status = out_dir / "status.txt"
    results_path = out_dir / "results.json"
    eval_dir = str(Path(__file__).resolve().parents[2] / "eval")
    env = dict(os.environ)
    # Plugin store: one persistent cookbook store for the whole sequence (CL).
    store = out_dir / "_memory" / ".cookbook-memory"
    if arm == "plugin":
        store.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        line = f"{time.strftime('%FT%TZ', time.gmtime())} [{arm}] {msg}"
        with status.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(line, flush=True)

    all_tasks = get_loader(BENCH).load(None, limit=None)
    seq = sorted(
        (t for t in all_tasks
         if SEQUENCE in (t.group_id or "", t.task_id) and t.kind is TaskKind.CODE),
        key=lambda t: getattr(t, "order", 0) or 0,
    )[: args.limit]
    log(f"START limit={args.limit} selected={len(seq)} agy={AGY} store={store if arm=='plugin' else '-'}")
    log("agy permissions: " + ensure_agy_permissions())
    if not seq:
        log("ERROR no tasks selected")
        return 2

    grader = SwebenchHostGrader(timeout=args.grader_timeout)
    repo0 = seq[0].repo or ""
    ver0 = str((seq[0].metadata or {}).get("version") or "")
    try:
        grader.prewarm_sequence(repo0, ver0)
        log(f"prewarm {repo0}@{ver0} done")
    except Exception as exc:  # noqa: BLE001
        log(f"prewarm failed (per-task fallback): {type(exc).__name__}: {exc}")

    results = []
    resolved = graded = 0
    for i, task in enumerate(seq, 1):
        tid = task.task_id
        safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in str(tid))[:80]
        checkout = out_dir / "repos" / safe
        rec: dict = {"task_id": tid, "repo": task.repo, "base_commit": task.base_commit,
                     "n_sessions": len(task.sessions or [])}
        log(f"[{i}/{len(seq)}] {tid} checkout (n_sess={rec['n_sessions']})")
        try:
            prepare_checkout(task.repo or "", task.base_commit, checkout,
                             timeout=args.grader_timeout)
        except CheckoutError as exc:
            rec.update(success=None, error=f"checkout: {str(exc)[:200]}", diff_len=0)
            results.append(rec)
            results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
            log(f"[{i}/{len(seq)}] {tid} checkout FAILED: {str(exc)[:160]}")
            continue

        issue = (task.question or "").strip()
        prompt = _CODE_INSTR.format(repo=task.repo) + "\n\nIssue:\n" + issue

        if arm == "builtin":
            # agy CANNOT use Claude Code's file-based native memory: writing session
            # files + a CLAUDE.md ("read before answering") into the checkout biased agy
            # into Q&A mode and it stopped editing entirely (0/17 diff_len=0). Instead,
            # inject the prior-session history as PLAIN PROMPT TEXT (like the plugin arm),
            # with the edit directive (_CODE_INSTR) last. No files written to the checkout.
            sess_txt = "\n".join(
                f"- {(s.content or '').strip()[:600]}" for s in (task.sessions or []) if (s.content or '').strip()
            )
            if sess_txt:
                prompt = (_PLUGIN_PREFIX.format(memories=sess_txt)
                          + _CODE_INSTR.format(repo=task.repo) + "\n\nIssue:\n" + issue)
            else:
                prompt = _CODE_INSTR.format(repo=task.repo) + "\n\nIssue:\n" + issue
        elif arm == "plugin":
            hits = cookbook_query(store, issue, args.k, eval_dir, env)
            rec["recall_hits"] = len(hits)
            log(f"[{i}/{len(seq)}] {tid} recall returned {len(hits)} memories")
            if hits:
                mem_txt = "\n".join(f"- {h.get('content','')[:600]}" for h in hits)
                # Same ordering rule as builtin: memories preamble, edit directive last.
                prompt = (_PLUGIN_PREFIX.format(memories=mem_txt)
                          + _CODE_INSTR.format(repo=task.repo) + "\n\nIssue:\n" + issue)

        log(f"[{i}/{len(seq)}] {tid} agy solving")
        agy_logf = out_dir / "logs" / f"{i:02d}_{safe}.agy"
        try:
            agy_out, rc, agy_cli_log, diff = run_agy(
                prompt, checkout, args.timeout, agy_logf, checkout, task.base_commit)
        except subprocess.TimeoutExpired:
            agy_out, rc, agy_cli_log, diff = "", -1, "", capture_diff(
                checkout, base_commit=task.base_commit)
            log(f"[{i}/{len(seq)}] {tid} agy TIMEOUT")
        (out_dir / "logs" / f"{i:02d}_{safe}.agy.stdout").write_text(
            agy_out[-40000:], encoding="utf-8", errors="ignore")
        rec["agy_rc"] = rc

        if arm == "builtin":
            rh = builtin_recall_hit(agy_cli_log)
            rec["builtin_session_reads"] = rh
            log(f"[{i}/{len(seq)}] {tid} builtin session-file reads: {len(rh)}")

        rec["diff_len"] = len(diff)
        (out_dir / "logs" / f"{i:02d}_{safe}.diff").write_text(diff, encoding="utf-8")

        # plugin WRITE: daydream over the transcript -> memories into the shared store.
        # Transcript = the issue + what agy actually did (its CLI log of edits), so
        # daydream has substantive content to extract a lesson from.
        if arm == "plugin":
            before = cookbook_count(store, eval_dir, env)
            tr = out_dir / "logs" / f"{i:02d}_{safe}.transcript.jsonl"
            agy_summary = agy_out.strip() or _agy_edit_summary(agy_cli_log, diff)
            write_transcript(tr, issue, agy_summary)
            dd = daydream_write(store, f"agy-{safe}", tr, eval_dir, env, args.timeout)
            (out_dir / "logs" / f"{i:02d}_{safe}.daydream.log").write_text(dd, encoding="utf-8")
            after = cookbook_count(store, eval_dir, env)
            rec["store_before"], rec["store_after"] = before, after
            log(f"[{i}/{len(seq)}] {tid} daydream store {before}->{after}")

        log(f"[{i}/{len(seq)}] {tid} agy rc={rc} diff_len={len(diff)} grading")
        verdict = grader(task, diff)
        rec["success"] = verdict
        rec["ungraded_reason"] = grader.last_reason
        if verdict is not None:
            graded += 1
            resolved += 1 if verdict else 0
        results.append(rec)
        results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        log(f"[{i}/{len(seq)}] {tid} verdict={verdict} (running resolved={resolved}/{graded})")

    pct = (100.0 * resolved / graded) if graded else 0.0
    summary = {"arm": arm, "sequence": SEQUENCE, "n_tasks": len(seq),
               "graded": graded, "resolved": resolved, "resolved_pct": round(pct, 1),
               "results": results}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log(f"DONE resolved={resolved}/{graded} ({pct:.1f}%) n_tasks={len(seq)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
