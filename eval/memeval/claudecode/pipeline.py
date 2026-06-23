"""The 5-stage SWE-Bench-CL pipeline driven by the live cookbook-memory plugin.

Installed as ``memeval-pipeline`` (and ``python -m memeval.claudecode.pipeline``). Runs,
over the same X tasks of ONE named SWE-Bench-CL sequence, five stages that together test
whether an accumulating + dream-consolidated memory makes the agent get better over time:

  1. base          -- mode=off, no plugin (the baseline)
  2. plugin-blank   -- plugin-real, empty shared memory substrate
  3. plugin-accum   -- plugin-real, the SAME substrate (now holding stage-2's memory)
  4. dream          -- one real whole-store consolidation pass over the shared substrate
                       (memeval.dreaming.worker.dream, the daydream-cli dream --all surface);
                       a no-op in practice until the TTL/contradiction/governance knobs are on
  5. plugin-dreamed -- plugin-real, the SAME substrate (final; reflects any dream mutations)

Memory is ONE shared substrate per pipeline VERSION at ``results/v{version}/_memory/``
(ADR-eval-003): the harness only ensures that directory exists and points
``CLAUDE_PROJECT_DIR`` at it; the plugin owns everything inside. Accumulation across
stages 2->3->5 happens purely because the directory persists -- the harness never copies,
seeds, or prunes the store. The version is the git tag on HEAD (ADR-eval-004).

The wrapper is interactive by default (offer + confirm the defaults) with a
non-interactive ``--yes`` mode for CI/scripts. Drives the SAME machinery the per-dev
``memeval-bench`` uses (``ClaudeCodeAgent`` + ``run_agent`` + ``LocalExecGrader``).
"""

from __future__ import annotations

import argparse
import os
import sys
import types
from pathlib import Path
from typing import Any, Optional

from .. import MEMORY_VERSION
from ..cost import DEFAULT_BUDGET_USD
from ..schema import Benchmark

_BENCHMARK = "swe_bench_cl"

#: The 8 SWE-Bench-CL sequences (the "domains"), largest first; sizes for the prompt.
_SEQUENCES = {
    "django_django_sequence": 50,
    "sympy_sympy_sequence": 50,
    "sphinx-doc_sphinx_sequence": 44,
    "matplotlib_matplotlib_sequence": 34,
    "scikit-learn_scikit-learn_sequence": 32,
    "astropy_astropy_sequence": 22,
    "pydata_xarray_sequence": 22,
    "pytest-dev_pytest_sequence": 19,
}
_DEFAULT_SEQUENCE = "pytest-dev_pytest_sequence"  # smallest -> cheapest to iterate
_DEFAULT_LIMIT = 20

#: The eval stages (the dream stage runs between accum and dreamed; it is not an eval).
_EVAL_STAGES = ("base", "plugin-blank", "plugin-accum", "plugin-dreamed")
_STAGE_INDEX = {"base": 1, "plugin-blank": 2, "plugin-accum": 3, "plugin-dreamed": 5}
_STAGE_MODE = {
    "base": "off",
    "plugin-blank": "plugin-real",
    "plugin-accum": "plugin-real",
    "plugin-dreamed": "plugin-real",
}


# --------------------------------------------------------------------------- #
# Config resolution + interactive wrapper
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="memeval-pipeline",
        description="Run the 5-stage SWE-Bench-CL pipeline against the live cookbook-memory "
                    "plugin: base -> plugin/blank -> plugin/accumulated -> dream -> "
                    "plugin/dreamed, sharing ONE persistent per-version memory substrate, "
                    "then write a base->final summary.",
    )
    ap.add_argument("-y", "--yes", "--non-interactive", dest="yes", action="store_true",
                    help="Non-interactive: use flags where given, defaults otherwise; no prompts.")
    ap.add_argument("--sequence", default=None,
                    help=f"SWE-Bench-CL sequence (the Y domain). One of: "
                         f"{', '.join(_SEQUENCES)}. Default {_DEFAULT_SEQUENCE}.")
    ap.add_argument("--limit", type=int, default=None,
                    help=f"How many tasks of the sequence to run (by Task.order). "
                         f"Default {_DEFAULT_LIMIT}; 0 = the whole sequence.")
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--code-mode", choices=["blind", "agentic"], default="agentic")
    ap.add_argument("--grader", default="local",
                    help="CODE grader: 'local' (host test execution; the real resolve "
                         "rate), 'overlap' (cheap heuristic), or 'none'.")
    ap.add_argument("--grader-timeout", type=int, default=1800)
    ap.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD)
    ap.add_argument("--plugin-workers", type=int, default=1,
                    help="Concurrency for plugin stages (default 1; the plugin MCP "
                         "connection degrades under headless concurrency).")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--path", default=None, help="Dataset path/id (blank = real source).")
    ap.add_argument("--results-dir", default="results",
                    help="Root for results/v{version}/ (and the shared _memory/ substrate).")
    ap.add_argument("--native-cl", dest="native_cl", action="store_true", default=False,
                    help="Capture paper-native CL metrics per eval stage (default off).")
    ap.add_argument("--no-native-cl", dest="native_cl", action="store_false")
    ap.add_argument("--skip-base", action="store_true",
                    help="Skip stage 1 (the memoryless 'base' baseline). Useful when "
                         "iterating on the plugin path — base is slow and unaffected by "
                         "plugin/memory changes. Its results row is omitted (no base→final "
                         "delta in the summary).")
    ap.add_argument("--stages", default=None,
                    help="Comma-separated subset of eval stages to run, in order "
                         "(base,plugin-blank,plugin-accum,plugin-dreamed). Overrides "
                         "--skip-base. E.g. --stages plugin-blank,plugin-accum.")
    return ap


def _interactive() -> bool:
    """True only when we have a real TTY to prompt on (so piped/CI runs fall through)."""
    return bool(sys.stdin and sys.stdin.isatty())


def _ask(label: str, default: Any, *, cast=str, choices: "list[str] | None" = None) -> Any:
    """One validated prompt with a default (Enter accepts it). Re-prompts on a bad value
    instead of crashing; ``cast`` parses the answer (int/float/str) and ``choices``
    constrains it. Falls through to the default on a non-tty."""
    if not _interactive():
        return default
    hint = f" ({'/'.join(choices)})" if choices else ""
    while True:
        raw = input(f"  {label}{hint} [{default}]: ").strip()
        if not raw:
            return default
        if choices and raw not in choices:
            print(f"    ! choose one of: {', '.join(choices)}")
            continue
        try:
            return cast(raw)
        except (ValueError, TypeError):
            print(f"    ! expected {cast.__name__}, got {raw!r}")


def _ask_sequence(default: str) -> str:
    """Numbered menu for the SWE-Bench-CL sequence — type a number (1-8) or the id.
    Enter accepts the default. Non-tty -> the default."""
    if not _interactive():
        return default
    seqs = list(_SEQUENCES.items())
    print("  sequence (the SWE-Bench-CL 'domain' — type a number or id):")
    for i, (name, size) in enumerate(seqs, 1):
        marker = " (default)" if name == default else ""
        print(f"    {i}. {name}  ·  {size} tasks{marker}")
    while True:
        raw = input(f"  sequence [{default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(seqs):
            return seqs[int(raw) - 1][0]
        if raw in _SEQUENCES:
            return raw
        print(f"    ! enter 1-{len(seqs)} or a valid sequence id")


def _resolve_config(args: argparse.Namespace) -> dict:
    """Resolve the run config from flags + (when interactive) prompts. Any flag passed
    explicitly pre-fills its prompt default; --yes skips all prompts."""
    seq = args.sequence or _DEFAULT_SEQUENCE
    limit = _DEFAULT_LIMIT if args.limit is None else args.limit
    model = args.model
    grader = args.grader
    budget = args.budget_usd
    skip_base = bool(args.skip_base)

    if not args.yes and _interactive():
        print("\nConfigure the 5-stage SWE-Bench-CL pipeline — press Enter to accept each default.\n")
        seq = _ask_sequence(seq)
        limit = _ask("tasks to run (0 = whole sequence)", limit, cast=int)
        model = _ask("model", model)
        grader = _ask("grader", grader, choices=["local", "overlap", "none"])
        budget = _ask("budget (USD, 0 = no cap)", budget, cast=float)
        if not args.stages:
            skip_default = "y" if skip_base else "n"
            skip_base = _ask("skip stage 1 base run?", skip_default,
                             choices=["y", "n"]).lower() in ("y", "yes")
        print()

    if seq not in _SEQUENCES:
        raise SystemExit(f"unknown --sequence {seq!r}; choose one of {list(_SEQUENCES)}")

    # Which eval stages to run (in canonical order). --stages wins; else --skip-base drops
    # the memoryless baseline.
    if args.stages:
        requested = [s.strip() for s in args.stages.split(",") if s.strip()]
        bad = [s for s in requested if s not in _EVAL_STAGES]
        if bad:
            raise SystemExit(f"unknown --stages {bad}; choose from {list(_EVAL_STAGES)}")
        stages = [s for s in _EVAL_STAGES if s in requested]  # canonical order, deduped
    else:
        stages = [s for s in _EVAL_STAGES if not (skip_base and s == "base")]

    return {
        "sequence": seq,
        "limit": None if int(limit) <= 0 else int(limit),
        "model": model,
        "grader": grader,
        "budget_usd": budget,
        "code_mode": args.code_mode,
        "grader_timeout": args.grader_timeout,
        "plugin_workers": args.plugin_workers,
        "timeout": args.timeout,
        "path": args.path,
        "results_dir": args.results_dir,
        "native_cl": args.native_cl,
        "stages": stages,
    }


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
def _dream_meta() -> dict:
    """The dreamer (subconscious) model recorded for provenance (ADR-dreaming-004)."""
    return {
        "provider": os.environ.get("DREAM_PROVIDER", "openrouter"),
        "model": os.environ.get("DREAM_MODEL", "inclusionai/ling-2.6-flash"),
    }


def _pipeline_meta(cfg: dict, version_info: dict, substrate: Path, stamp: str) -> dict:
    return {
        "version": version_info["version"],
        "version_exact": version_info.get("version_exact"),
        "untagged": version_info.get("untagged"),
        "git_sha": version_info.get("git_sha", ""),
        "sequence": cfg["sequence"],          # the Y domain -- NOT in memory anymore
        "limit": cfg["limit"],
        "n_tasks": None,                       # filled from the first stage's actual count
        "model": cfg["model"],
        "code_mode": cfg["code_mode"],
        "grader": cfg["grader"],
        "plugin_workers": cfg["plugin_workers"],
        "budget_usd": cfg["budget_usd"],
        "dream": _dream_meta(),
        "memory_store": str(substrate),
        "n_stages": 5,
        "n_eval_stages": len(_EVAL_STAGES),
        "stages": ["base", "plugin-blank", "plugin-accum", "dream", "plugin-dreamed"],
        "timestamp": stamp,
        "started_at": None,                    # epoch seconds, set when the run starts
        "ended_at": None,                      # epoch seconds, set when the run ends
    }


def _make_agent(stage: str, cfg: dict, substrate: Path):
    """Build the ClaudeCodeAgent for a stage. Plugin stages share the ONE substrate
    (project_dir); the base stage has no memory."""
    from .agent import ClaudeCodeAgent

    mode = _STAGE_MODE[stage]
    return ClaudeCodeAgent(
        model=cfg["model"], memory_mode=mode, code_mode=cfg["code_mode"],
        timeout=cfg["timeout"],
        project_dir=substrate if mode == "plugin-real" else None,
    )


def _grader(cfg: dict):
    """Resolve the CODE grader, reusing run_bench's resolver via a tiny args shim."""
    from .run_bench import _make_grader
    shim = types.SimpleNamespace(grader=cfg["grader"], grader_timeout=cfg["grader_timeout"])
    return _make_grader(_BENCHMARK, shim)


def _stage_task_total(cfg: dict) -> int:
    """How many tasks this stage will run — the sequence's size capped by ``--limit`` —
    so progress can be shown as ``[done/total]`` (the loader does the same selection)."""
    from ..loaders import get_loader

    n = sum(1 for t in get_loader(Benchmark.from_str(_BENCHMARK)).load(cfg["path"], limit=None)
            if str(t.group_id or "") == cfg["sequence"])
    return min(n, cfg["limit"]) if cfg["limit"] else n


def _rebase_cost(rr: Any, cost_base: float) -> Any:
    """Subtract the pre-stage cumulative spend so ``rr.cost_usd`` is THIS stage's cost,
    not the running pipeline total (the CostTracker is shared for one budget cap).
    Tokens are already per-stage (summed from the run's own trajectories)."""
    try:
        rr.cost_usd = max(0.0, rr.cost_usd - cost_base)
    except Exception:  # noqa: BLE001
        pass
    return rr


def _make_progress_cb(stage: str, total: int, on_task=None, cost_base: float = 0.0):
    """A run_agent progress callback that, after EACH completed task, (1) prints
    ``[N/total] stage … resolved · $cost`` so a long stage isn't a silent wait, and
    (2) calls ``on_task(partial)`` so the caller can persist the in-progress results to
    disk. run_agent invokes it after each task with the partial RunResult
    (n_tasks = completed so far)."""
    import sys
    import time

    state = {"last": 0, "t0": time.monotonic()}

    def cb(partial: Any) -> None:
        done = partial.n_tasks
        if done <= state["last"]:
            return  # only act on forward progress (callback may fire on each worker)
        state["last"] = done
        _rebase_cost(partial, cost_base)  # show THIS stage's cost, not the pipeline total
        elapsed = int(time.monotonic() - state["t0"])
        resolved = sum(1 for t in partial.trajectories if t.success)
        print(f"  [{done}/{total}] {stage}: {resolved} resolved · "
              f"${partial.cost_usd:.4f} · {elapsed}s elapsed", flush=True)
        sys.stdout.flush()
        if on_task is not None:
            try:  # persisting partial results must never break the run
                on_task(partial)
            except Exception:  # noqa: BLE001
                pass

    return cb


def _run_eval_stage(stage: str, cfg: dict, substrate: Path, *, cost: Any,
                    total: int, on_task=None, cost_base: float = 0.0) -> Any:
    """Run one eval stage through ``run_agent`` (the same machinery memeval-bench uses)
    and return its ``RunResult``. Prints per-task progress and (via ``on_task``) lets the
    caller write partial results after each task so the results file appears early.
    ``cost_base`` is the cumulative spend at stage start, subtracted so reported cost is
    per-stage (the CostTracker is shared for one budget cap)."""
    from ..agent import run_agent

    agent = _make_agent(stage, cfg, substrate)
    workers = cfg["plugin_workers"] if _STAGE_MODE[stage] == "plugin-real" else 1
    return run_agent(
        Benchmark.from_str(_BENCHMARK), agent,
        memory=(_STAGE_MODE[stage] != "off"),
        limit=cfg["limit"], sequence=cfg["sequence"],
        path_or_id=cfg["path"], cost=cost, grader=_grader(cfg),
        progress_cb=_make_progress_cb(stage, total, on_task=on_task, cost_base=cost_base),
        seed_sessions=False, workers=workers,
    )


def _native_cl_for_stage(stage: str, cfg: dict, substrate: Path) -> Optional[dict]:
    """Compute the paper-native CL report for an eval stage and return its dict.

    NOTE: the native evaluator runs its OWN mem-on / re-test / mem-off A/B over the
    sequence, and assumes a per-sequence memory reset -- which the shared accumulating
    substrate deliberately breaks. The report is captured for comparison and flagged in
    the summary; it is not the pipeline's primary metric (resolve-rate accuracy is)."""
    from ..loaders import get_loader
    from ..native.registry import get_native_evaluator

    bench = Benchmark.from_str(_BENCHMARK)
    tasks = [t for t in get_loader(bench).load(cfg["path"], limit=None)
             if str(t.group_id or "") == cfg["sequence"]]
    tasks.sort(key=lambda t: int(t.order))
    if cfg["limit"]:
        tasks = tasks[: cfg["limit"]]
    if not tasks:
        return None
    evaluator = get_native_evaluator(bench)
    agent = _make_agent(stage, cfg, substrate)
    records = evaluator.run(tasks, agent_or_model=agent, mode=_STAGE_MODE[stage],
                            grader=cfg["grader"])
    report = evaluator.score(records, tasks)
    d = report.to_dict()
    d.setdefault("metadata", {})["caveat"] = (
        "native CL assumes per-sequence memory reset; the pipeline's shared substrate "
        "accumulates across stages, so these are comparative, not paper-faithful"
    )
    return d


def _run_dream_stage(substrate: Path) -> dict:
    """Dream stage -- run ONE real whole-store consolidation pass over the shared substrate.

    Wired to the SAME surface the plugin's ``daydream-cli dream --all`` uses: build the
    RouterStore for the store dir (``cookbook_memory.core.contract.build_store`` via the
    dreaming CLI's ``_make_store`` seam, ADR-harness-011) and run
    ``memeval.dreaming.worker.dream`` against it under the basedir dream-lock. The worker
    is real (Jobs 1-4: TTL prune, dedup, contradiction, governance) and returns a
    ``dream.summary`` dict, which the pipeline summary renders.

    "Stub no-op in practice": with the destructive passes disabled by default
    (``DREAM_ITEM_RETENTION_DAYS=0`` / ``DREAM_CONTRADICTION_MAX_CALLS=0`` /
    ``DREAM_GOVERNANCE_MAX_CALLS=0``, or simply no ``OPENROUTER_API_KEY`` for the LLM
    passes), a single pass over the accumulated store deletes nothing but exercises the
    full wiring and records a real summary -- so the stage-3->5 delta stays ~0 until those
    knobs are turned on, but the consolidation surface is now genuinely invoked.

    The store dir is ``<substrate>/.cookbook-memory`` -- the SAME path the plugin stages
    write to (``ClaudeCodeAgent._plugin_real_store`` sets ``MEMORY_STORE`` there); the
    worker resolves its basedir from ``$MEMORY_STORE`` (ADR-dreaming-019). Fail-open: any
    failure (lock contended, unsupported FS, worker error) returns a structured
    ``skipped``/``error`` dict and NEVER aborts the pipeline."""
    import os

    from ..dreaming import _state, worker
    from ..dreaming.cli import _make_store

    store_dir = (substrate / ".cookbook-memory").resolve()
    store_dir.mkdir(parents=True, exist_ok=True)

    prev = os.environ.get("MEMORY_STORE")
    os.environ["MEMORY_STORE"] = str(store_dir)
    try:
        store = _make_store(store_dir)
        return worker.dream(store=store)
    except _state._DreamLockHeld:
        return {"status": "skipped", "reason": "dream basedir lock contended",
                "store": str(store_dir)}
    except _state._UnsupportedFsError as exc:
        return {"status": "skipped",
                "reason": f"unsupported filesystem ({exc}); set DREAM_ALLOW_NETWORK_FS=1",
                "store": str(store_dir)}
    except Exception as exc:  # noqa: BLE001 -- dreaming must never abort the pipeline
        return {"status": "error", "error_type": type(exc).__name__,
                "reason": str(exc)[:200], "store": str(store_dir)}
    finally:
        if prev is None:
            os.environ.pop("MEMORY_STORE", None)
        else:
            os.environ["MEMORY_STORE"] = prev


def _sandbox_auth_probe(config_dir: Path, *, timeout: int = 60) -> bool:
    """Actually verify the sandbox is authenticated by driving ``claude -p "ok"`` against
    it. This is the only reliable check on keychain platforms (macOS), where the token is
    NOT an on-disk file — so a stale/absent token isn't caught by inspecting files. API-key
    env vars are stripped so the CLI uses the OAuth subscription, not an API key. Returns
    True only on a clean exit whose output isn't a 'not logged in' error."""
    import os
    import subprocess

    from .platform import detect

    rt = detect()
    exe = rt.exe if rt else "claude"
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    env["CLAUDE_CONFIG_DIR"] = str(config_dir.resolve())
    try:
        proc = subprocess.run([exe, "-p", "ok"], env=env, timeout=timeout,
                              capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    out = (proc.stdout + proc.stderr).lower()
    if proc.returncode != 0:
        return False
    return "not logged in" not in out and "/login" not in out and "invalid api key" not in out


def _ensure_sandbox_ready() -> None:
    """Make EVERY stage use the isolated sandbox CLAUDE_CONFIG_DIR — never the host — and
    FAIL CLOSED before running anything if it isn't authenticated.

    The harness sandboxes ``claude`` so a run never picks up the host's skills / agents /
    CLAUDE.md / auth. But ``active_config_dir()`` only returns the sandbox once it has been
    BUILT (its ``settings.json`` exists), and the sandbox was previously built lazily inside
    the first plugin-real stage — so stage 1 (base) ran against the HOST config. Build it up
    front here so all 5 stages resolve to the sandbox, then PROBE the sandbox's auth with a
    real ``claude -p`` turn and abort if it's logged out (a file check alone is a false
    positive on macOS, where the token lives in the keychain, not on disk).

    Skipped when the sandbox is explicitly disabled (``MEMEVAL_SANDBOX=0``) — an intentional
    opt-out — or when ``MEMEVAL_PIPELINE_SKIP_AUTH_PROBE`` is set (offline tests)."""
    import os

    from . import sandbox

    # Honor an explicit disable (the user opted out of the default sandbox).
    if (os.environ.get(sandbox.ENV_TOGGLE) or "").strip().lower() in {"0", "false", "no", "off"}:
        print("MEMEVAL_SANDBOX is off — running against the host claude config (not sandboxed).",
              file=sys.stderr)
        return

    d = Path(sandbox.active_config_dir() or sandbox.default_config_dir())
    if not sandbox.exists(d):
        sandbox.build(d)  # writes the minimal settings.json so active_config_dir() returns it

    if os.environ.get("MEMEVAL_PIPELINE_SKIP_AUTH_PROBE"):
        return  # offline tests: skip the network probe

    print(f"sandbox: {d} — verifying login before any stage runs…", flush=True)
    if not _sandbox_auth_probe(d):
        cmds = "\n  ".join(sandbox.login_commands(d))
        raise SystemExit(
            "\nThe benchmark sandbox is NOT logged in — aborting before any stage runs.\n\n"
            "Every pipeline stage runs against an ISOLATED claude config (never your host "
            "login), so the sandbox needs its own one-time authentication. It then PERSISTS "
            "across all future runs (one-time per machine, not per run):\n\n  " + cmds + "\n"
        )
    print(f"sandbox: {d} (logged in) — all stages use this, not the host claude.", flush=True)


def _warn_if_memory_cannot_accumulate() -> None:
    """Warn LOUDLY when the daydreamer can't extract memories — the whole point of the
    pipeline is that memory accumulates, and that requires an LLM to extract it.

    The plugin's daydreamer reads each session and calls an OpenRouter model to decide
    what to remember. With ``OPENROUTER_API_KEY`` unset it fail-opens to a NO-OP
    (ADR-dreaming-012): the store is created and daydream runs, but ZERO memories are
    written — so every plugin stage runs on empty memory and the base→final comparison is
    meaningless (memory never accumulates). This is the single most common reason a run
    'works' but shows no memory lift, so flag it prominently up front."""
    import os

    provider = os.environ.get("DREAM_PROVIDER", "openrouter").strip().lower()
    if provider == "openrouter" and not (os.environ.get("OPENROUTER_API_KEY") or "").strip():
        print(
            "\n" + "!" * 78 + "\n"
            "WARNING: OPENROUTER_API_KEY is not set — the daydreamer cannot extract memories.\n"
            "The plugin store will be created and daydream will run, but ZERO memories will be\n"
            "written (fail-open no-op, ADR-dreaming-012). Every plugin stage then runs on EMPTY\n"
            "memory, so the base→final comparison shows no accumulation — the experiment is a\n"
            "no-op. Set OPENROUTER_API_KEY (or DREAM_PROVIDER) before a real run.\n"
            + "!" * 78 + "\n",
            file=sys.stderr, flush=True)


def run_pipeline(cfg: dict) -> dict:
    """Run all 5 stages and write the results + summary. Returns the summary dict."""
    from ..cost import CostTracker
    from ..results import resolve_pipeline_version, run_timestamp
    from . import pipeline_summary as PS

    import time

    _ensure_sandbox_ready()  # MUST be first — every stage uses the sandbox, never the host
    _warn_if_memory_cannot_accumulate()  # OPENROUTER_API_KEY gates daydream memory extraction
    version_info = resolve_pipeline_version()
    version = version_info["version"]
    stamp = run_timestamp()
    results_root = Path(cfg["results_dir"])
    substrate = (results_root / version / "_memory").resolve()
    substrate.mkdir(parents=True, exist_ok=True)  # the harness's ONLY store responsibility

    meta = _pipeline_meta(cfg, version_info, substrate, stamp)
    meta["started_at"] = time.time()
    print(f"pipeline v{version.lstrip('v')} · sequence {cfg['sequence']} · "
          f"limit {cfg['limit']} · model {cfg['model']}")
    print(f"shared memory substrate: {substrate}")
    if version_info.get("untagged"):
        src = version_info.get("source")
        if src == "branch":
            note = (f"NOTE: HEAD is untagged -> version keyed by branch "
                    f"'{version_info.get('branch')}' ({version}). This branch's memory "
                    f"accumulates here; tag the commit for an archival, comparable run.")
        else:
            note = ("NOTE: HEAD is untagged and detached/branchless -> version fell back to "
                    f"MEMORY_VERSION (v{MEMORY_VERSION}); tag the commit for a comparable run.")
        print(note, file=sys.stderr)

    rows: list[dict] = []
    native_by_stage: dict[str, dict] = {}
    dream: Optional[dict] = None
    in_progress: dict[str, Any] = {"stage": None, "row": None}

    def _write_partial() -> None:
        # Completed stage rows + the in-progress stage's partial row (if any), so the
        # results file exists from the first task and grows live — not only between stages.
        live = list(rows)
        if in_progress["row"] is not None:
            live.append(in_progress["row"])
        PS.write_pipeline_results(benchmark=_BENCHMARK, version=version, timestamp=stamp,
                                  rows=live, pipeline_meta=meta, dream=dream,
                                  root=str(results_root))

    def _on_task(stage: str, partial: Any) -> None:
        # After each task within a stage, refresh that stage's partial row and rewrite
        # the results file so progress is visible on disk immediately.
        in_progress["stage"] = stage
        in_progress["row"] = PS.stage_row(partial, stage=stage,
                                          stage_index=_STAGE_INDEX[stage], pipeline_meta=meta)
        _write_partial()

    active = cfg.get("stages") or list(_EVAL_STAGES)
    meta["stages_run"] = active
    cost = CostTracker(budget_usd=cfg["budget_usd"]) if cfg["budget_usd"] and cfg["budget_usd"] > 0 else None
    total = _stage_task_total(cfg)
    meta["n_tasks"] = total
    nat = " + native-CL passes" if cfg["native_cl"] else ""
    skipped = [s for s in _EVAL_STAGES if s not in active]
    skip_note = f" (skipping: {', '.join(skipped)})" if skipped else ""
    print(f"running {len(active)} eval stage(s){skip_note} × {total} tasks{nat} "
          f"(plugin stages run {cfg['plugin_workers']} at a time)…", flush=True)
    _write_partial()  # create the results file immediately (header + pipeline metadata)
    print(f"results file: {PS.benchmark_results_path(_BENCHMARK, version=version, timestamp=stamp, root=str(results_root))}",
          flush=True)

    # Pre-dream eval stages (base / plugin-blank / plugin-accum), in canonical order.
    for stage in ("base", "plugin-blank", "plugin-accum"):
        if stage not in active:
            continue
        row = _run_one(stage, cfg, substrate, cost, native_by_stage, meta, total,
                       on_task=lambda p, s=stage: _on_task(s, p))
        rows.append(row)
        in_progress["row"] = None  # stage finished -> its final row is in `rows`
        _write_partial()

    # Stage 4 (dream) + stage 5 (plugin-dreamed) only run if the final stage is selected.
    if "plugin-dreamed" in active:
        print("\n── stage 4/5 · dream ──────────", flush=True)
        print("  running whole-store consolidation (daydream-cli dream --all surface)…",
              flush=True)
        dream = _run_dream_stage(substrate)
        _dream_status = dream.get("status") or dream.get("mode") or "ok"
        _dream_counts = dream.get("counts") or {}
        if _dream_counts:
            print(f"  ✓ dream: {_dream_status} · {_dream_counts.get('total_items', 0)} items · "
                  f"{_dream_counts.get('items_retired', 0)} deduped · "
                  f"{_dream_counts.get('items_pruned', 0)} pruned · "
                  f"{_dream_counts.get('items_contradicted', 0)} contradicted · "
                  f"{_dream_counts.get('items_blacklisted', 0)} blacklisted", flush=True)
        else:
            print(f"  dream: {_dream_status} — {dream.get('reason', 'no consolidation')}",
                  flush=True)
        _write_partial()

        rows.append(_run_one("plugin-dreamed", cfg, substrate, cost, native_by_stage, meta, total,
                             on_task=lambda p: _on_task("plugin-dreamed", p)))
        in_progress["row"] = None
    meta["ended_at"] = time.time()

    path = PS.write_pipeline_results(benchmark=_BENCHMARK, version=version, timestamp=stamp,
                                     rows=rows, pipeline_meta=meta, dream=dream,
                                     root=str(results_root))
    summary = PS.build_summary(benchmark=_BENCHMARK, rows=rows, pipeline_meta=meta,
                               dream=dream, native_by_stage=native_by_stage)
    md_path, json_path = PS.write_summary(benchmark=_BENCHMARK, version=version,
                                          timestamp=stamp, summary=summary,
                                          root=str(results_root))
    print(f"\nresults: {path}")
    print(f"summary: {md_path}")
    print(PS.render_summary_md(summary))
    return summary


def _run_one(stage: str, cfg: dict, substrate: Path, cost: Any,
             native_by_stage: dict, meta: dict, total: int, on_task=None) -> dict:
    """Run one eval stage (standard metrics + optional native CL) and return its row.
    Prints a stage banner, per-task progress (via run_agent's callback), and a summary.
    ``on_task`` is forwarded so partial results can be persisted after each task."""
    import time

    from . import pipeline_summary as PS

    idx = _STAGE_INDEX[stage]
    t0 = time.monotonic()
    # The CostTracker is shared across stages (one --budget-usd cap for the whole run),
    # so its spent_usd is CUMULATIVE. Subtract the spend at stage start so each stage's
    # cost reflects only THAT stage, not the running pipeline total.
    cost_base = cost.spent_usd if cost is not None else 0.0
    print(f"\n── stage {idx}/5 · {stage} (mode={_STAGE_MODE[stage]}) · {total} tasks "
          f"──────────", flush=True)
    rr = _run_eval_stage(stage, cfg, substrate, cost=cost, total=total, on_task=on_task,
                         cost_base=cost_base)
    _rebase_cost(rr, cost_base)
    m = rr.metrics
    secs = int(time.monotonic() - t0)
    resolved = sum(1 for t in rr.trajectories if t.success)
    print(f"  ✓ stage {idx} done · {resolved}/{rr.n_tasks} resolved · acc={m.accuracy:.3f} "
          f"rel={m.relevancy:.3f} eff={m.efficiency:.3f} · ${rr.cost_usd:.4f} · {secs}s",
          flush=True)
    if cfg["native_cl"]:
        print(f"  computing native CL metrics for stage {idx} "
              f"(mem-on / re-test / mem-off passes)…", flush=True)
        try:
            report = _native_cl_for_stage(stage, cfg, substrate)
            if report is not None:
                native_by_stage[stage] = report
                print(f"  ✓ native CL captured for stage {idx}", flush=True)
        except Exception as exc:  # native CL is supplementary -- never abort the stage
            print(f"  native CL skipped: {type(exc).__name__}: {str(exc)[:120]}",
                  file=sys.stderr, flush=True)
    return PS.stage_row(rr, stage=stage, stage_index=idx, pipeline_meta=meta)


def main(argv: Optional[list[str]] = None) -> int:
    from ..dotenv_loader import load_root_dotenv
    from .platform import describe, detect

    load_root_dotenv(verbose=True)  # FIRST — set OPENROUTER_API_KEY etc. before any check reads them
    args = _build_parser().parse_args(argv)
    cfg = _resolve_config(args)

    print(describe())
    if detect() is None:
        print("WARNING: 'claude' not found — plugin stages will fail until it is installed "
              "(npm install -g @anthropic-ai/claude-code).", file=sys.stderr)

    if not args.yes and _interactive():
        limit_txt = "whole sequence" if cfg["limit"] is None else f"{cfg['limit']} tasks"
        budget_txt = "no cap" if not cfg["budget_usd"] else f"${cfg['budget_usd']:.0f}"
        print("About to run the 5-stage pipeline:")
        print(f"  sequence  {cfg['sequence']}  ·  {limit_txt}")
        print(f"  model     {cfg['model']}  ·  grader {cfg['grader']}  ·  budget {budget_txt}")
        print(f"  native CL {'on' if cfg['native_cl'] else 'off'}")
        ans = _ask("run these 5 stages now?", "y", choices=["y", "n"]).lower()
        if ans not in ("y", "yes"):
            print("aborted.")
            return 0

    run_pipeline(cfg)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
