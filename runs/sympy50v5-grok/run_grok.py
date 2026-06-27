"""Driver: grok as agent-under-test over the Sympy SWE-Bench-CL sequence, all 3 arms.

Mirrors the claude run in runs/sympy3 (same sequence sympy_sympy_sequence, same
--limit, same SwebenchHostGrader) but the solver is grok, and the memory mechanisms
are orchestrated EXTERNALLY (grok has no Claude hooks):

  base    — no memory; solve from the issue prompt only.
  builtin — prior sessions written as sessions/*.md in the checkout; grok greps/reads
            them before solving (its native file tools). Recall attributed from grok's
            own chat_history.jsonl transcript (honest file-read detection).
  plugin  — BEFORE each task: recall top-k from the cookbook store (RouterStore.search)
            and inject into the prompt. AFTER each task: daydream-extract memories from
            grok's transcript into the SAME store (dreaming CLI). Store persists across
            the sequence -> continual learning, exactly what the Claude plugin does via
            its Stop hook. Uses the plugin's OWN surfaces only.

Usage (from eval/, WSL):
  uv run --no-project python ../runs/sympy3-grok/run_grok.py --arm plugin
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

RUNDIR = Path("/mnt/c/Users/kenhu/agent-memory-harness/runs/sympy50v5-grok")
LIMIT = int(os.environ.get("GROK_LIMIT", "15"))
TIMEOUT = int(os.environ.get("GROK_TIMEOUT", "1800"))


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["base", "builtin", "plugin"])
    args = ap.parse_args()
    arm = args.arm

    armdir = RUNDIR / arm
    armdir.mkdir(parents=True, exist_ok=True)
    status = armdir / "status.txt"
    results_path = armdir / "results.json"
    # plugin store persists across the sequence (the continual-learning substrate).
    store_dir = armdir / ".cookbook-memory"

    def log_status(msg: str) -> None:
        with status.open("a", encoding="utf-8") as fh:
            fh.write(f"{msg}\n")

    status.write_text(
        f"DRIVER_START={now()} arm={arm} limit={LIMIT} "
        f"orklen={len(os.environ.get('OPENROUTER_API_KEY',''))} "
        f"DREAM_EXTRACTION_VARIANT={os.environ.get('DREAM_EXTRACTION_VARIANT','UNSET')}\n",
        encoding="utf-8")

    from memeval.claudecode import grok_runner as gr
    from memeval.grader_swebench import SwebenchHostGrader
    from memeval.loaders.swe_bench_cl import SWEBenchCLLoader

    tasks = [t for t in SWEBenchCLLoader().load()
             if (t.group_id or "").startswith("sympy")][:LIMIT]
    log_status(f"TASKS_LOADED n={len(tasks)} seq={tasks[0].group_id}")

    grader = SwebenchHostGrader(model_name=f"grok-{arm}", timeout=TIMEOUT)
    repo = tasks[0].repo or ""
    version = str((tasks[0].metadata or {}).get("version") or "")
    log_status(f"PREWARM repo={repo}@{version} {now()}")
    py = grader.prewarm_sequence(repo, version)
    log_status(f"PREWARM_DONE python={py} {now()}")

    results = []
    resolved = 0
    work_root = Path(tempfile.mkdtemp(prefix=f"grok-{arm}-"))

    for i, task in enumerate(tasks, 1):
        log_status(f"TASK_START [{i}/{len(tasks)}] {task.task_id} {now()}")
        t0 = time.time()
        checkout_root = work_root / task.task_id
        checkout_root.mkdir(parents=True, exist_ok=True)
        checkout = checkout_root / "repo"

        # -- per-arm prompt + pre-task memory ---------------------------------- #
        recalled: list[str] = []
        prompt = None
        if arm == "base":
            prompt = gr.build_grok_prompt(task.question)
        elif arm == "builtin":
            prompt = gr.build_builtin_prompt(task.question)
        elif arm == "plugin":
            cnt_before = gr.store_count(store_dir)
            recalled = gr.plugin_recall(store_dir, task.question, k=5)
            prompt = gr.build_plugin_prompt(task.question, recalled)
            log_status(f"  RECALL task={task.task_id} store_count={cnt_before} "
                       f"recalled={len(recalled)}")

        # builtin needs sessions/*.md laid down in the checkout BEFORE the turn;
        # prepare the checkout here so we can write them, then pass prompt to solve.
        from memeval.claudecode.checkout import CheckoutError, prepare_checkout
        n_sessions = 0
        try:
            prepare_checkout(task.repo or "", task.base_commit, checkout, timeout=TIMEOUT)
            if arm == "builtin":
                n_sessions = gr.write_session_files(checkout, task)
        except CheckoutError as exc:
            res = {"task_id": task.task_id, "error": f"checkout failed: {exc}",
                   "resolved": None, "diff_len": 0}
            results.append(res)
            log_status(f"TASK_DONE [{i}/{len(tasks)}] {task.task_id} CHECKOUT_FAIL")
            continue

        # -- the grok turn (checkout already materialized) --------------------- #
        rc, stdout, stderr = gr.run_grok(prompt, checkout, timeout=TIMEOUT)
        diff = gr.capture_tracked_diff(checkout)
        verdict = grader(task, diff)
        dt = round(time.time() - t0, 1)

        res = {
            "task_id": task.task_id, "arm": arm, "grok_rc": rc,
            "diff_len": len(diff), "resolved": verdict, "elapsed_s": dt,
            "ungraded_reason": getattr(grader, "last_reason", None) if verdict is None else None,
            "recalled_count": len(recalled), "n_sessions": n_sessions,
        }

        # -- per-arm post-task bookkeeping ------------------------------------- #
        if arm == "builtin":
            hits = gr.builtin_recall_hits(checkout, task)
            res["builtin_recall_hits"] = hits
            log_status(f"  BUILTIN_RECALL task={task.task_id} "
                       f"sessions={n_sessions} read_hits={len(hits)} {hits[:3]}")
        if arm == "plugin":
            # Build a compact transcript for daydream: the issue + grok's answer + the diff.
            transcript = (
                f"USER: {task.question}\n\n"
                f"ASSISTANT (grok): {stdout}\n\n"
                f"PATCH APPLIED:\n{diff[:8000]}\n"
            )
            added = gr.daydream_write(store_dir, task.task_id, transcript, python_exe=sys.executable)
            res["daydream_added"] = added
            res["store_count_after"] = gr.store_count(store_dir)
            log_status(f"  DAYDREAM task={task.task_id} added={added} "
                       f"store_count_after={res['store_count_after']}")

        if verdict is True:
            resolved += 1
        results.append(res)

        # Per-task log (full diff + stdout tail).
        (armdir / f"task-{task.task_id}.log").write_text(json.dumps({
            **res, "diff": diff[:300000], "grok_stdout_tail": (stdout or "")[-3000:],
            "recalled": recalled,
        }, indent=2), encoding="utf-8")
        results_path.write_text(json.dumps({
            "agent": "grok", "arm": arm, "sequence": tasks[0].group_id,
            "limit": len(tasks), "resolved": resolved, "graded_so_far": i,
            "results": results,
        }, indent=2), encoding="utf-8")
        log_status(f"TASK_DONE [{i}/{len(tasks)}] {task.task_id} "
                   f"resolved={verdict} diff_len={len(diff)} rc={rc} {dt}s "
                   f"running_resolved={resolved}")

    log_status(f"DRIVER_DONE arm={arm} resolved={resolved}/{len(tasks)} {now()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
