"""The single-stage memory pipeline driven by the live cookbook-memory plugin.

Installed as ``memeval-pipeline`` (and ``python -m memeval.claudecode.pipeline``). Runs
ONE eval stage over the X tasks of ONE named sequence — a SWE-Bench-CL sequence (a
per-repo task chain), or a single VISTA journey (one of the six) — against the persistent
per-version memory substrate. The stages a run can pick from (one per invocation) are:

  * base           -- mode=off, no plugin (the memoryless baseline)
  * builtin        -- Claude Code's native file-based memory over prior sessions
  * plugin-blank   -- plugin-real against the shared memory substrate
  * plugin-accum   -- plugin-real seeded from a selected previous run's memory substrate,
                      then evaluated in this run's own versioned namespace
  * plugin-dreamed -- plugin-real seeded from a selected previous run's memory substrate,
                      after ONE dreaming pass over the substrate
  * plugin-primed  -- plugin-real with natural recall and primed stream-json invocation

The previous five-stage orchestration (base -> blank -> accum -> dream -> dreamed in one
go, with base->final deltas) is gone: each run is a single stage so a sequence can be
driven one stage at a time, and stages compose across separate invocations through the
persistent substrate rather than within one run.

Memory is ONE shared substrate per pipeline VERSION at ``results/v{version}/_memory/``
(ADR-eval-003): the harness ensures that directory exists and points
``CLAUDE_PROJECT_DIR`` at it; the plugin owns everything inside. ``plugin-accum`` and
``plugin-dreamed`` seed the run's fresh namespace from a selected previous run's
``_memory`` folder before evaluation. The version is the git tag on HEAD, or
branch+commit SHA for untagged runs unless ``--results-version`` explicitly names a
bucket (ADR-eval-004).

The wrapper is interactive by default (offer + confirm the defaults) with a
non-interactive ``--yes`` mode for CI/scripts. Drives the SAME machinery the per-dev
``memeval-bench`` uses (``ClaudeCodeAgent`` + ``run_agent`` + ``LocalExecGrader``).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import types
from pathlib import Path
from typing import Any, Optional

from .. import MEMORY_VERSION
from ..cost import DEFAULT_BUDGET_USD
from ..schema import Benchmark

#: The benchmarks the pipeline can drive, each with its selectable sequences mapped to an
#: approximate task count for the menu, plus a default. A sequence is matched by
#: ``group_id`` OR ``task_id`` (ADR-eval): SWE-Bench-CL sequences are per-repo task chains
#: (matched by group_id); VISTA's are its six individual journeys (matched by task_id, one
#: task each).
_BENCHMARKS: dict[str, dict[str, Any]] = {
    "swe_bench_cl": {
        "label": "SWE-Bench-CL",
        # The 8 SWE-Bench-CL sequences, largest first; sizes for the prompt.
        "sequences": {
            "django_django_sequence": 50,
            "sympy_sympy_sequence": 50,
            "sphinx-doc_sphinx_sequence": 44,
            "matplotlib_matplotlib_sequence": 34,
            "scikit-learn_scikit-learn_sequence": 32,
            "astropy_astropy_sequence": 22,
            "pydata_xarray_sequence": 22,
            "pytest-dev_pytest_sequence": 19,
        },
        "default_sequence": "pytest-dev_pytest_sequence",  # smallest -> cheapest
        "unit": "sequence",
    },
    "vista": {
        "label": "VISTA",
        "unit": "journey",
        # The six VISTA journeys, each its own selectable sequence (one journey == one
        # Task, matched by task_id). The journey's domain (project/coding/research) is
        # preserved on the task's ``competency`` and used by the native evaluator; here
        # each journey is run on its own.
        "sequences": {
            "project-stewardship-inquiry-001": 1,
            "coding-pr-review-001": 1,
            "research-synthesis-001": 1,
            "synth-project-train-001": 1,
            "synth-coding-train-001": 1,
            "synth-research-train-001": 1,
        },
        "default_sequence": "coding-pr-review-001",
    },
}
_DEFAULT_BENCHMARK = "swe_bench_cl"
_DEFAULT_LIMIT = 0
_DEFAULT_MODEL = "claude-haiku-4-5"
_MODEL_CHOICES = (
    ("claude-haiku-4-5", "fastest / cheapest default"),
    ("claude-sonnet-4-6", "stronger coding model"),
    ("claude-opus-4-8", "highest-quality model"),
)
#: Cursor CLI (cursor-agent) model menu — shown when `--harness cursor` is selected.
#: cursor-agent accepts these ids (verify the live list with `cursor-agent --list-models`).
_DEFAULT_CURSOR_MODEL = "composer-2.5"
_CURSOR_MODEL_CHOICES = (
    ("composer-2.5", "Cursor's own model (fast default)"),
    ("gpt-5.5-high", "OpenAI GPT-5.5 (1M, high)"),
    ("claude-opus-4-8-thinking-high", "Anthropic Opus 4.8 (1M, thinking)"),
)


def _bench_spec(benchmark: str) -> dict[str, Any]:
    try:
        return _BENCHMARKS[benchmark]
    except KeyError:
        raise SystemExit(
            f"unknown --benchmark {benchmark!r}; choose one of {list(_BENCHMARKS)}"
        ) from None


def _sequences(benchmark: str) -> dict[str, int]:
    return _bench_spec(benchmark)["sequences"]


def _default_sequence(benchmark: str) -> str:
    return _bench_spec(benchmark)["default_sequence"]


def _git_short_sha(cwd: "str | Path | None" = None) -> str:
    """The current short commit SHA, or ``"nogit"`` when git is unavailable."""
    import subprocess
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=str(cwd) if cwd else None, capture_output=True,
                             text=True, timeout=10, check=False)
    except Exception:  # noqa: BLE001 - slug provenance, never fatal
        return "nogit"
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "nogit"


def _slugify(token: str) -> str:
    """Filesystem-safe token: non ``[A-Za-z0-9._-]`` -> ``-``, runs squeezed, trimmed."""
    import re
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(token)).strip("-")
    return re.sub(r"-{2,}", "-", safe) or "x"


def _default_version_slug(cfg: dict, results_dir: "str | Path") -> str:
    """The default version slug for a run: ``sequence-type-sha-int`` (ADR-eval-004's
    per-version substrate, but keyed to THIS run rather than the branch).

    * ``sequence`` -- the selected sequence / VISTA group id
    * ``type``     -- the run type (the selected stage)
    * ``sha``      -- the current short git SHA (``nogit`` when unavailable)
    * ``int``      -- a dedup integer: the lowest ``>= 1`` whose results directory does
                      not already exist, so a brand-new run gets a fresh substrate while
                      the slug stays short when there's no collision.

    The slug is what the interactive prompt offers as the default and what the user can
    edit to reuse a prior substrate. It is passed to ``resolve_pipeline_version`` as the
    explicit version override (``normalize_version`` adds the ``v`` prefix)."""
    from ..results import normalize_version

    base = f"{_slugify(cfg['sequence'])}-{_slugify(cfg['stage'])}-{_slugify(_git_short_sha())}"
    root = Path(results_dir)
    for i in range(1, 10_000):
        candidate = f"{base}-{i}"
        if not (root / normalize_version(candidate)).exists():
            return candidate
    return f"{base}-{1}"  # pathological: fall back to -1


def _pipeline_docs(version_dir: Path) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for path in sorted(version_dir.glob("*.json")):
        if path.name.startswith("SUMMARY-"):
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc.get("pipeline"), dict):
            docs.append(doc)
    return docs


def _memory_source_candidates(
    results_dir: "str | Path", *, benchmark: str, sequence: str,
    exclude_version: "str | None" = None,
) -> list[dict[str, Any]]:
    """Previous run memory folders matching this benchmark+sequence, newest first."""
    from ..results import normalize_version

    root = Path(results_dir)
    excluded = normalize_version(exclude_version) if exclude_version else None
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for version_dir in root.iterdir():
        if not version_dir.is_dir() or not version_dir.name.startswith("v"):
            continue
        if excluded and version_dir.name == excluded:
            continue
        memory = version_dir / "_memory"
        if not memory.is_dir():
            continue
        matches = []
        for doc in _pipeline_docs(version_dir):
            pipe = doc.get("pipeline") or {}
            if pipe.get("benchmark") == benchmark and pipe.get("sequence") == sequence:
                matches.append(doc)
        if not matches:
            continue
        latest_doc = max(
            matches,
            key=lambda d: str((d.get("pipeline") or {}).get("timestamp") or d.get("timestamp") or ""),
        )
        pipe = latest_doc.get("pipeline") or {}
        health = _store_health(memory)
        try:
            child_mtimes = [p.stat().st_mtime for p in memory.rglob("*") if p.exists()]
            mtime = max([memory.stat().st_mtime, *child_mtimes])
        except OSError:
            mtime = version_dir.stat().st_mtime
        out.append({
            "version": version_dir.name,
            "path": str(memory.resolve()),
            "stage": pipe.get("stage"),
            "timestamp": pipe.get("timestamp") or latest_doc.get("timestamp"),
            "mtime": mtime,
            "durable_items": health.get("durable_items", 0),
        })
    return sorted(out, key=lambda c: c["mtime"], reverse=True)


def _resolve_memory_source(raw: str, results_dir: "str | Path") -> Path:
    """Resolve a user-supplied source as either a path or a results version slug."""
    from ..results import normalize_version

    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    if p.exists():
        if p.name == ".cookbook-memory":
            p = p.parent
        if not (p / ".cookbook-memory").is_dir():
            raise SystemExit(
                f"selected memory folder {p} does not contain .cookbook-memory"
            )
        return p.resolve()
    by_version = Path(results_dir) / normalize_version(raw) / "_memory"
    if by_version.is_dir():
        return by_version.resolve()
    raise SystemExit(
        f"selected memory source {raw!r} was not found as a path or results version"
    )


#: The eval stages a run can pick from (exactly one per invocation). Each stage IS a
#: pipeline mode — the variation of the run. ``plugin-dreamed`` runs a dream
#: consolidation pass over the substrate before it evaluates.
_EVAL_STAGES = (
    "base",
    "builtin",
    "plugin-blank",
    "plugin-accum",
    "plugin-dreamed",
    "plugin-primed",
)
_DEFAULT_STAGE = "plugin-accum"

#: Human-facing one-line descriptions of each mode (stage) for the interactive menu.
_MODE_LABELS = {
    "base": "no plugin — the memoryless baseline (mode=off)",
    "builtin": "Claude Code native file-based memory over prior sessions",
    "plugin-blank": "plugin-real starting from blank memory",
    "plugin-accum": "plugin-real seeded from an existing memory store",
    "plugin-dreamed": "plugin-real seeded from existing memory, then dreamed before eval",
    "plugin-primed": "plugin-real, natural recall prompt, primed stream-json invocation",
}
_STAGE_INDEX = {name: i for i, name in enumerate(_EVAL_STAGES, 1)}
_STAGE_MODE = {
    "base": "off",
    "builtin": "builtin",
    "plugin-blank": "plugin-real",
    "plugin-accum": "plugin-real",
    "plugin-dreamed": "plugin-real",
    "plugin-primed": "plugin-real",
}
_STAGE_PLUGIN_REAL_OPTIONS = {
    "plugin-primed": {
        "plugin_real_invocation": "primed",
    },
}
_SOURCE_MEMORY_STAGES = {"plugin-accum", "plugin-dreamed"}


# --------------------------------------------------------------------------- #
# Config resolution + interactive wrapper
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="memeval-pipeline",
        description="Run ONE eval stage over ONE sequence against the live cookbook-memory "
                    "plugin and the persistent per-version memory substrate. Pick the "
                    "benchmark (swe_bench_cl or vista), the sequence (a SWE-Bench-CL "
                    "sequence or one of the six VISTA journeys), and the stage.",
    )
    ap.add_argument("-y", "--yes", "--non-interactive", dest="yes", action="store_true",
                    help="Non-interactive: use flags where given, defaults otherwise; no prompts.")
    ap.add_argument("--benchmark", default=None, choices=list(_BENCHMARKS),
                    help=f"Which benchmark to drive: {', '.join(_BENCHMARKS)}. "
                         f"Default {_DEFAULT_BENCHMARK}.")
    ap.add_argument("--stage", default=None, choices=list(_EVAL_STAGES),
                    help=f"The single eval stage to run (one per invocation): "
                         f"{', '.join(_EVAL_STAGES)}. Default {_DEFAULT_STAGE}. "
                         f"'plugin-dreamed' runs a dream consolidation pass first.")
    ap.add_argument("--sequence", default=None,
                    help="Sequence to run — depends on --benchmark. SWE-Bench-CL: a "
                         "per-repo sequence id (a chain of tasks); VISTA: a single "
                         "journey id (one of the six journeys).")
    ap.add_argument("--limit", type=int, default=None,
                    help=f"How many tasks of the sequence to run (by Task.order). "
                         f"Default {_DEFAULT_LIMIT}; 0 = the whole sequence.")
    ap.add_argument("--harness", choices=["claude", "cursor"], default="claude",
                    help="Which agent CLI to drive (ADR-harness-013): 'claude' "
                         "(default, Claude Code) or 'cursor' (the Cursor CLI; needs "
                         "CURSOR_API_KEY in .env — see .env.example). Same stages, "
                         "benchmarks, graders, and shared substrate either way.")
    ap.add_argument("--model", default=_DEFAULT_MODEL)
    ap.add_argument("--code-mode", choices=["blind", "agentic"], default="agentic")
    ap.add_argument("--grader", default="swebench",
                    help="CODE grader: 'swebench' (default: Docker-free grader reusing "
                         "SWE-bench's own specs + log parsers; needs the 'swebench' "
                         "extra), 'auto' (local test execution for SWE tasks), 'local' "
                         "(host test execution; the real resolve rate), 'overlap' (cheap "
                         "heuristic), or 'none'.")
    ap.add_argument("--grader-timeout", type=int, default=1800)
    ap.add_argument("--grader-python", action="append", default=[],
                    metavar="PIN=PYTHON",
                    help="Exact interpreter for SWE-bench host grading, e.g. "
                         "--grader-python 3.6=/opt/python/3.6/bin/python. "
                         "Repeat for multiple pins.")
    ap.add_argument("--allow-python-substitution", action="store_true",
                    help="Allow the SWE-bench host grader to use the nearest newer "
                         "uv-managed Python when the pinned Python is unavailable. "
                         "This is host-substitution and is not leaderboard-comparable.")
    ap.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD)
    ap.add_argument("--plugin-workers", type=int, default=1,
                    help="Concurrency for plugin stages (default 1; the plugin MCP "
                         "connection degrades under headless concurrency).")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--path", default=None, help="Dataset path/id (blank = real source).")
    ap.add_argument("--results-dir", default="results",
                    help="Root for results/v{version}/ (and the shared _memory/ substrate).")
    ap.add_argument("--results-version", default=None,
                    help="Explicit results/memory version slug (the substrate bucket). "
                         "Default is a 'sequence-type-sha-int' slug — the selected "
                         "sequence, the run type (stage), the current git SHA, and a "
                         "dedup integer that bumps when a dir of that exact name already "
                         "exists — offered as the interactive prompt default. Pass a "
                         "prior slug only when you intentionally want to write into that "
                         "same namespace.")
    ap.add_argument("--source-memory", default=None,
                    help="For plugin-accum and plugin-dreamed: existing memory folder or "
                         "results version to seed this run from. Accepts a path to "
                         "_memory, a path to .cookbook-memory, or a results version slug. "
                         "Defaults to the newest prior memory folder for the selected "
                         "benchmark+sequence.")
    ap.add_argument("--native-cl", dest="native_cl", action="store_true", default=False,
                    help="Capture paper-native CL metrics for the stage (default off).")
    ap.add_argument("--no-native-cl", dest="native_cl", action="store_false")
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


def _ask_benchmark(default: str) -> str:
    """Numbered menu for the benchmark. Enter accepts the default; non-tty -> default."""
    if not _interactive():
        return default
    names = list(_BENCHMARKS)
    print("  benchmark (type a number or id):")
    for i, name in enumerate(names, 1):
        marker = " (default)" if name == default else ""
        print(f"    {i}. {name}  ·  {_BENCHMARKS[name]['label']}{marker}")
    while True:
        raw = input(f"  benchmark [{default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            return names[int(raw) - 1]
        if raw in _BENCHMARKS:
            return raw
        print(f"    ! enter 1-{len(names)} or one of: {', '.join(names)}")


def _ask_mode(default: str) -> str:
    """Numbered menu for the pipeline mode (the stage / run variation) — type a number
    or the id. Enter accepts the default. Non-tty -> the default."""
    if not _interactive():
        return default
    print("  mode (the pipeline variation — type a number or id):")
    for i, name in enumerate(_EVAL_STAGES, 1):
        marker = " (default)" if name == default else ""
        print(f"    {i}. {name}  ·  {_MODE_LABELS[name]}{marker}")
    while True:
        raw = input(f"  mode [{default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(_EVAL_STAGES):
            return _EVAL_STAGES[int(raw) - 1]
        if raw in _EVAL_STAGES:
            return raw
        print(f"    ! enter 1-{len(_EVAL_STAGES)} or one of: {', '.join(_EVAL_STAGES)}")


def _ask_sequence(benchmark: str, default: str) -> str:
    """Numbered menu for the sequence (the benchmark's run unit) — type a number or the
    id. Enter accepts the default. Non-tty -> the default."""
    if not _interactive():
        return default
    seqs = list(_sequences(benchmark).items())
    spec = _bench_spec(benchmark)
    unit = spec.get("unit", "sequence")
    print(f"  {unit} (the {spec['label']} run unit — type a number or id):")
    for i, (name, size) in enumerate(seqs, 1):
        marker = " (default)" if name == default else ""
        plural = "task" if size == 1 else "tasks"
        print(f"    {i}. {name}  ·  {size} {plural}{marker}")
    while True:
        raw = input(f"  {unit} [{default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(seqs):
            return seqs[int(raw) - 1][0]
        if raw in _sequences(benchmark):
            return raw
        print(f"    ! enter 1-{len(seqs)} or a valid {unit} id")


def _ask_harness(default: str) -> str:
    """Numbered menu for the agent CLI to drive (claude | cursor)."""
    if not _interactive():
        return default
    choices = (
        ("claude", "Claude Code (claude-agent) — default"),
        ("cursor", "Cursor CLI (cursor-agent) — needs CURSOR_API_KEY in .env"),
    )
    print("  harness (which agent CLI to drive — type a number or id):")
    names = [name for name, _ in choices]
    for i, (name, label) in enumerate(choices, 1):
        marker = " (default)" if name == default else ""
        print(f"    {i}. {name}  ·  {label}{marker}")
    while True:
        raw = input(f"  harness [{default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            return names[int(raw) - 1]
        if raw in names:
            return raw
        print(f"    choose one of {names} (or a number)")


def _ask_model(default: str, *, harness: str = "claude") -> str:
    """Numbered menu for common models. Typed custom model ids are accepted. The menu
    is harness-aware: Cursor (cursor-agent) gets its own model list, not Claude's."""
    if not _interactive():
        return default
    choices = _CURSOR_MODEL_CHOICES if harness == "cursor" else _MODEL_CHOICES
    print("  model (type a number or model id):")
    names = [name for name, _label in choices]
    for i, (name, label) in enumerate(choices, 1):
        marker = " (default)" if name == default else ""
        print(f"    {i}. {name}  ·  {label}{marker}")
    while True:
        raw = input(f"  model [{default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            return names[int(raw) - 1]
        return raw


def _ask_memory_source(stage: str, candidates: list[dict[str, Any]], default: str) -> str:
    """Numbered menu for stages that require a prior memory folder."""
    if not _interactive():
        return default
    by_path = {str(c["path"]): c for c in candidates}
    default_candidate = by_path.get(default)
    default_label = (
        str(default_candidate.get("version")) if default_candidate else Path(default).name
    )
    print(f"  source memory for {stage} (type a number, version, or path):")
    for i, c in enumerate(candidates, 1):
        marker = " (default)" if c["path"] == default else ""
        stage = c.get("stage") or "unknown-stage"
        timestamp = c.get("timestamp") or "unknown-time"
        print(f"    {i}. {c['version']}  ·  {timestamp}  ·  {stage}{marker}")
    while True:
        raw = input(f"  source memory [{default_label}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(candidates):
            return str(candidates[int(raw) - 1]["path"])
        for c in candidates:
            if raw == c.get("version"):
                return str(c["path"])
        return raw


def _resolve_config(args: argparse.Namespace) -> dict:
    """Resolve the run config from flags + (when interactive) prompts. Any flag passed
    explicitly pre-fills its prompt default; --yes skips all prompts."""
    benchmark = args.benchmark or _DEFAULT_BENCHMARK
    # The sequence default tracks the benchmark, so an explicit --sequence overrides but a
    # bare --benchmark still lands on a valid sequence for that benchmark.
    seq = args.sequence or _default_sequence(benchmark)
    stage = args.stage or _DEFAULT_STAGE
    limit = _DEFAULT_LIMIT if args.limit is None else args.limit
    harness = getattr(args, "harness", "claude")
    model = args.model
    model_is_default = model == _DEFAULT_MODEL  # no explicit --model given
    grader = args.grader
    budget = args.budget_usd
    # An explicit --results-version always wins; otherwise the version is the
    # sequence-type-sha-int slug (offered as the prompt default for this run's output
    # namespace). Computed below once sequence + stage are final.
    results_version = args.results_version
    source_memory = args.source_memory

    if not args.yes and _interactive():
        print("\nConfigure the single-stage memory pipeline — press Enter to accept each default.\n")
        harness = _ask_harness(harness)
        benchmark = _ask_benchmark(benchmark)
        if not args.sequence:  # a benchmark switch should reset the sequence default
            seq = _default_sequence(benchmark)
        seq = _ask_sequence(benchmark, seq)
        stage = _ask_mode(stage)
        limit = _ask("tasks to run (0 = whole sequence)", limit, cast=int)
        # Offer the right model default + menu for the chosen harness (the Claude id is
        # meaningless to cursor-agent). An explicit --model always wins.
        if harness == "cursor" and model_is_default:
            model = _DEFAULT_CURSOR_MODEL
        model = _ask_model(model, harness=harness)
        grader = _ask("grader", grader,
                      choices=["auto", "local", "swebench", "overlap", "none"])
        budget = _ask("budget (USD, 0 = no cap)", budget, cast=float)
    elif harness == "cursor" and model_is_default:
        # Non-interactive (--yes / no TTY): still swap the default model for cursor.
        model = _DEFAULT_CURSOR_MODEL

    if benchmark not in _BENCHMARKS:
        raise SystemExit(f"unknown --benchmark {benchmark!r}; choose one of {list(_BENCHMARKS)}")
    if seq not in _sequences(benchmark):
        raise SystemExit(
            f"unknown --sequence {seq!r} for benchmark {benchmark!r}; "
            f"choose one of {list(_sequences(benchmark))}")
    if stage not in _EVAL_STAGES:
        raise SystemExit(f"unknown --stage {stage!r}; choose one of {list(_EVAL_STAGES)}")

    # Version slug: ask + accept it interactively, defaulting to sequence-type-sha-int.
    # (--results-version, when given, is authoritative and skips the prompt + default.)
    if results_version is None:
        cfg_for_slug = {"sequence": seq, "stage": stage}
        default_slug = _default_version_slug(cfg_for_slug, args.results_dir)
        if not args.yes and _interactive():
            results_version = _ask("version slug (where this run writes)", default_slug)
        else:
            results_version = default_slug

    if stage in _SOURCE_MEMORY_STAGES:
        candidates = _memory_source_candidates(
            args.results_dir,
            benchmark=benchmark,
            sequence=seq,
            exclude_version=results_version,
        )
        usable_candidates = [c for c in candidates if int(c.get("durable_items") or 0) > 0]
        if source_memory is None and usable_candidates:
            default_source = str(usable_candidates[0]["path"])
            if not args.yes and _interactive():
                source_memory = _ask_memory_source(stage, usable_candidates, default_source)
            else:
                source_memory = default_source
        elif source_memory is None and candidates:
            raise SystemExit(
                f"{stage} found previous memory folders for {benchmark} / {seq}, but "
                "none contained durable memories"
            )
        elif source_memory is None and not args.yes and _interactive():
            raise SystemExit(
                f"{stage} requires a pre-existing memory folder for this "
                f"benchmark+sequence ({benchmark} / {seq}), but none were found under "
                f"{args.results_dir!r}. Run plugin-blank first or pass --source-memory."
            )
        elif source_memory is None:
            raise SystemExit(
                f"{stage} requires --source-memory or a discoverable previous memory "
                f"folder for {benchmark} / {seq} under {args.results_dir!r}"
            )
        if source_memory is not None:
            resolved_source = _resolve_memory_source(source_memory, args.results_dir)
            _source_memory_health_or_die(resolved_source, stage)
            source_memory = str(resolved_source)

    if not args.yes and _interactive():
        print()

    return {
        "harness": harness,
        "benchmark": benchmark,
        "sequence": seq,
        "stage": stage,
        "limit": None if int(limit) <= 0 else int(limit),
        "model": model,
        "grader": grader,
        "budget_usd": budget,
        "code_mode": args.code_mode,
        "grader_timeout": args.grader_timeout,
        "grader_python": list(args.grader_python or []),
        "allow_python_substitution": bool(args.allow_python_substitution),
        "plugin_workers": args.plugin_workers,
        "timeout": args.timeout,
        "path": args.path,
        "results_dir": args.results_dir,
        "results_version": results_version,
        "source_memory": source_memory,
        "native_cl": args.native_cl,
    }


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
def _dream_meta() -> dict:
    """The dreamer (subconscious) model + extraction prompt recorded for provenance
    (ADR-dreaming-004).

    ``extraction_prompt`` is the *resolved* identity of the daydream extraction prompt
    this run would use — the variant key (``V0``..``V5``), the sha256 of its text, and its
    char count — read via ``resolve_extraction_prompt`` so it reflects the real
    ``DREAM_EXTRACTION_VARIANT`` resolution (default ``V0``, case-normalized), not a raw
    env echo. Recorded on EVERY run (not just plugin-dreamed) to document the substrate's
    lineage; the sha256 pins the exact text even if a variant's body later drifts. The
    per-memory ground truth remains the ``daydream.prompt_resolved`` diary events.
    Fail-open: provenance must never abort a run."""
    meta: dict[str, Any] = {
        "provider": os.environ.get("DREAM_PROVIDER", "openrouter"),
        "model": os.environ.get("DREAM_MODEL", "inclusionai/ling-2.6-flash"),
    }
    try:
        from ..dreaming.prompts import resolve_extraction_prompt

        ident = resolve_extraction_prompt()  # reads DREAM_EXTRACTION_VARIANT, default V0
        meta["extraction_prompt"] = {
            "variant": ident.variant,
            "sha256": ident.sha256,
            "char_count": ident.char_count,
        }
    except Exception as exc:  # noqa: BLE001 - provenance must never break a run
        meta["extraction_prompt"] = {
            "variant": None,
            "error": f"{type(exc).__name__}: {str(exc)[:120]}",
        }
    return meta


def _pipeline_meta(cfg: dict, version_info: dict, substrate: Path, stamp: str) -> dict:
    stage = cfg["stage"]
    runs_dream = stage == "plugin-dreamed"
    return {
        "version": version_info["version"],
        "version_exact": version_info.get("version_exact"),
        "untagged": version_info.get("untagged"),
        "git_sha": version_info.get("git_sha", ""),
        "harness": cfg.get("harness", "claude"),  # which agent CLI drove the run
        "benchmark": cfg["benchmark"],
        "sequence": cfg["sequence"],          # the Y domain -- NOT in memory anymore
        "stage": stage,                        # the single eval stage this run drove
        "limit": cfg["limit"],
        "n_tasks": None,                       # filled from the stage's actual count
        "model": cfg["model"],
        "code_mode": cfg["code_mode"],
        "grader": cfg["grader"],
        "plugin_workers": cfg["plugin_workers"],
        "budget_usd": cfg["budget_usd"],
        "dream": _dream_meta(),
        "memory_store": str(substrate),
        "source_memory": cfg.get("source_memory"),
        "source_memory_health": None,
        # Single-stage run: exactly one eval stage (plus an upfront dream pass for
        # plugin-dreamed). Kept for self-description; no cross-stage orchestration.
        "n_stages": 2 if runs_dream else 1,
        "n_eval_stages": 1,
        "stages": (["dream", stage] if runs_dream else [stage]),
        "timestamp": stamp,
        "started_at": None,                    # epoch seconds, set when the run starts
        "ended_at": None,                      # epoch seconds, set when the run ends
    }


def _make_agent(stage: str, cfg: dict, substrate: Path):
    """Build the stage's agent for ``cfg['harness']`` (ADR-harness-013). Plugin stages
    share the ONE substrate (project_dir); the base stage has no memory.

    ``claude`` (default) → ClaudeCodeAgent. ``cursor`` → CursorCodeAgent (the Cursor
    CLI sibling). Both satisfy AgentAdapter and feed the same run_agent/grader path.
    The Cursor adapter supports off/builtin/plugin-real; the plugin-blank/accum/dreamed
    stages map to plugin-real memory (their seeding/dreaming differences are driven by
    the shared substrate the same way)."""
    mode = _STAGE_MODE[stage]
    if cfg.get("harness", "claude") == "cursor":
        from ..cursorcli import CursorCodeAgent
        return CursorCodeAgent(
            model=cfg["model"], memory_mode=mode, code_mode=cfg["code_mode"],
            timeout=cfg["timeout"],
            project_dir=substrate if mode == "plugin-real" else None,
        )
    from .agent import ClaudeCodeAgent
    plugin_opts = _STAGE_PLUGIN_REAL_OPTIONS.get(stage, {})
    return ClaudeCodeAgent(
        model=cfg["model"], memory_mode=mode, code_mode=cfg["code_mode"],
        timeout=cfg["timeout"],
        project_dir=substrate if mode == "plugin-real" else None,
        **plugin_opts,
    )


def _grader(cfg: dict):
    """Resolve the CODE grader, reusing run_bench's resolver via a tiny args shim."""
    from .run_bench import _make_grader
    shim = types.SimpleNamespace(
        grader=cfg["grader"],
        grader_timeout=cfg["grader_timeout"],
        grader_python=cfg.get("grader_python") or [],
        allow_python_substitution=bool(cfg.get("allow_python_substitution")),
    )
    return _make_grader(cfg["benchmark"], shim)


def _in_sequence(task: Any, sequence: str) -> bool:
    """Whether ``task`` belongs to the selected sequence. A sequence matches a
    ``group_id`` (a SWE-Bench-CL sequence) OR a ``task_id`` (a single VISTA journey, the
    selectable unit there) — mirrors ``run_agent``'s sequence filter."""
    return (str(getattr(task, "group_id", "") or "") == sequence
            or str(getattr(task, "task_id", "") or "") == sequence)


def _stage_task_total(cfg: dict) -> int:
    """How many tasks this stage will run — the sequence's size capped by ``--limit`` —
    so progress can be shown as ``[done/total]`` (the loader does the same selection)."""
    from ..loaders import get_loader

    bench = Benchmark.from_str(cfg["benchmark"])
    n = sum(1 for t in get_loader(bench).load(cfg["path"], limit=None)
            if _in_sequence(t, cfg["sequence"]))
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


def _sqlite_count(path: Path, table: str) -> Optional[int]:
    """Return a SQLite table count, or ``None`` when the store/table is absent."""
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(str(path)) as db:
            row = db.execute(f"select count(*) from {table}").fetchone()
    except sqlite3.Error:
        return None
    return int(row[0]) if row else None


def _event_name(rec: dict) -> str:
    return str(rec.get("op") or rec.get("event_type") or rec.get("event") or rec.get("type") or "")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _read_store_events(store_dir: Path) -> list[dict]:
    return _read_jsonl(store_dir / "events.jsonl")


def _read_daydream_events(store_dir: Path) -> list[dict]:
    dream_dir = store_dir / "dream"
    if not dream_dir.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(dream_dir.glob("*.daydream-events.jsonl")):
        out.extend(_read_jsonl(path))
    return out


def _store_health(substrate: Path) -> dict[str, Any]:
    """Read-only observability snapshot for the plugin-owned shared store."""
    store_dir = substrate / ".cookbook-memory"
    events = _read_store_events(store_dir)
    daydream_events = _read_daydream_events(store_dir)
    recall_events = [r for r in events if _event_name(r) == "recall"]
    recall_with_hits = [
        r for r in recall_events
        if (r.get("ids") or (r.get("meta") or {}).get("hits"))
    ]
    names = [_event_name(r) for r in events]
    daydream_names = [_event_name(r) for r in daydream_events]
    all_names = [*names, *daydream_names]
    memory_items = _sqlite_count(store_dir / "memory.db", "items")
    graph_nodes = _sqlite_count(store_dir / "graph.db", "nodes")
    markdown_items = len(list(store_dir.rglob("*.md"))) if store_dir.is_dir() else 0
    durable_counts = [n for n in (memory_items, graph_nodes, markdown_items) if n is not None]
    return {
        "store_dir": str(store_dir),
        "events": len(events),
        "daydream_events": len(daydream_events),
        "recall_events": len(recall_events),
        "recall_with_hits": len(recall_with_hits),
        "recall_zero_hits": len(recall_events) - len(recall_with_hits),
        "recall_errors": sum(
            1 for r in events
            if _event_name(r) == "error" and (r.get("meta") or {}).get("op_attempted") == "recall"
        ),
        "daydream_completed": sum(
            1 for n in all_names
            if n in {"daydream.hook_subprocess_fired", "daydream.chunk_extracted"}
        ),
        "daydream_memory_written": sum(1 for n in all_names if n == "daydream.memory_written"),
        "memory_items": memory_items,
        "graph_nodes": graph_nodes,
        "markdown_items": markdown_items,
        "durable_items": max(durable_counts) if durable_counts else 0,
    }


def _source_memory_health_or_die(source: Path, stage: str) -> dict[str, Any]:
    health = _store_health(source)
    if health.get("durable_items", 0) <= 0:
        raise SystemExit(
            f"{stage} requires a source memory store with durable memories, but "
            f"{source} is empty"
        )
    return health


def _copy_memory_dataset(source: Path, target: Path) -> None:
    """Copy the complete plugin-owned memory dataset, not just Markdown notes."""
    shutil.copytree(source, target, dirs_exist_ok=True)


def _seed_source_memory(cfg: dict, substrate: Path) -> Optional[dict[str, Any]]:
    """Copy a selected previous ``_memory`` dataset into this run namespace."""
    stage = str(cfg.get("stage") or "")
    if stage not in _SOURCE_MEMORY_STAGES:
        return None
    raw = cfg.get("source_memory")
    if not raw:
        raise SystemExit(
            f"{stage} requires --source-memory or a discoverable previous memory "
            "folder for the selected benchmark+sequence"
        )
    source = _resolve_memory_source(str(raw), cfg["results_dir"])
    substrate = substrate.resolve()
    if source == substrate:
        raise SystemExit(
            f"{stage} must write to its own versioned namespace; choose a "
            "different --results-version from --source-memory"
        )
    health = _source_memory_health_or_die(source, stage)
    substrate.mkdir(parents=True, exist_ok=True)
    _copy_memory_dataset(source, substrate)
    return {
        "path": str(source),
        "copied_to": str(substrate),
        "health": health,
    }


def _health_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, after_v in after.items():
        before_v = before.get(key)
        if isinstance(after_v, int) and isinstance(before_v, int):
            out[key] = after_v - before_v
    return out


def _stage_warnings(stage: str, cfg: dict, rr: Any, before: dict[str, Any],
                    after: dict[str, Any], delta: dict[str, Any]) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if _STAGE_MODE[stage] == "plugin-real":
        if stage in {"plugin-accum", "plugin-dreamed"} and after.get("durable_items", 0) <= 0:
            warnings.append({
                "code": "memory_store_empty",
                "message": "shared store has no durable memories after accumulation stage",
            })
        if delta.get("daydream_completed", 0) > 0 and delta.get("daydream_memory_written", 0) <= 0:
            warnings.append({
                "code": "daydream_completed_without_writes",
                "message": "daydream completed but wrote no durable memories",
            })
    if cfg["grader"] == "none" or rr.metadata.get("graded_n", 0) == 0:
        warnings.append({
            "code": "accuracy_ungraded",
            "message": "no graded tasks; accuracy is not a resolve-rate measurement",
        })
    elif rr.metadata.get("ungraded", 0) > 0:
        warnings.append({
            "code": "partial_grading",
            "message": (
                f"{rr.metadata.get('ungraded', 0)} of {rr.n_tasks} tasks were ungraded; "
                "accuracy denominator is graded tasks only"
            ),
        })
    return warnings


def _plugin_memory_probe() -> dict[str, Any]:
    """Best-effort plugin runtime + synthetic memory round-trip check."""
    from . import sandbox

    try:
        sandbox._require_plugin_mcp_runtime()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "code": "plugin_mcp_runtime_unavailable",
            "message": str(exc)[:240],
        }
    try:
        from cookbook_memory.core import MemoryClient

        with tempfile.TemporaryDirectory(prefix="cookbook-memory-preflight-") as tmp:
            client = MemoryClient(store=tmp)
            mem_id = client.remember("cookbook memory preflight sentinel",
                                     tags=["preflight"], ts=1.0)
            hits = client.recall("preflight sentinel", k=3, ts=2.0)
        return {
            "ok": bool(mem_id and hits),
            "code": "plugin_memory_round_trip_failed" if not (mem_id and hits) else "ok",
            "remembered": bool(mem_id),
            "hits": len(hits),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "code": "plugin_memory_round_trip_error",
            "message": f"{type(exc).__name__}: {str(exc)[:200]}",
        }


def _preflight(cfg: dict, substrate: Path, active: list[str]) -> dict[str, Any]:
    warnings: list[dict[str, str]] = []
    probe: Optional[dict[str, Any]] = None
    provider = os.environ.get("DREAM_PROVIDER", "openrouter").strip().lower()
    if provider == "openrouter" and not (os.environ.get("OPENROUTER_API_KEY") or "").strip():
        warnings.append({
            "code": "dreamer_key_missing",
            "message": "OPENROUTER_API_KEY is not set; daydream extraction may write no memories",
        })
    if cfg["grader"] == "none":
        warnings.append({
            "code": "grader_none",
            "message": "CODE accuracy will be ungraded",
        })
    if "plugin-blank" in active:
        initial = _store_health(substrate)
        if initial.get("durable_items", 0) > 0 or initial.get("events", 0) > 0:
            warnings.append({
                "code": "shared_store_not_blank",
                "message": "plugin-blank stage is starting with an existing shared store",
            })
    if any(_STAGE_MODE.get(stage) == "plugin-real" for stage in active):
        probe = _plugin_memory_probe()
        if not probe.get("ok"):
            warnings.append({
                "code": str(probe.get("code") or "plugin_preflight_failed"),
                "message": str(probe.get("message") or "plugin synthetic memory preflight failed"),
            })
    return {"warnings": warnings, "plugin_memory_probe": probe}


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
        resolved = sum(1 for t in partial.trajectories if t.success is True)
        graded = sum(1 for t in partial.trajectories if t.success is not None)
        ungraded = done - graded
        print(f"  [{done}/{total}] {stage}: {resolved}/{graded} graded resolved"
              f" · {ungraded} ungraded · "
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
    grader = _grader(cfg)
    _prewarm_sequence_venv(cfg, grader)  # build the sequence's shared venv ahead of its tasks
    return run_agent(
        Benchmark.from_str(cfg["benchmark"]), agent,
        memory=(_STAGE_MODE[stage] != "off"),
        limit=cfg["limit"], sequence=cfg["sequence"],
        path_or_id=cfg["path"], cost=cost, grader=grader,
        progress_cb=_make_progress_cb(stage, total, on_task=on_task, cost_base=cost_base),
        seed_sessions=False, workers=workers,
    )


def _prewarm_sequence_venv(cfg: dict, grader: Any) -> None:
    """Build the sequence's shared grading venv AHEAD of its tasks, when the resolved
    grader supports it (the SWE-bench host grader). Every task of a SWE-Bench-CL sequence
    shares one repo@version, so the interpreter + third-party deps are provisioned once
    here and reused across the sequence; per-task grading then only re-installs that task's
    checkout. No-op for graders without ``prewarm_sequence`` (overlap/local/none, VISTA's
    QA path) and fail-open: a prewarm failure just falls back to the per-task venv."""
    prewarm = getattr(grader, "prewarm_sequence", None)
    if not callable(prewarm):
        return
    from ..loaders import get_loader

    bench = Benchmark.from_str(cfg["benchmark"])
    seq_tasks = [t for t in get_loader(bench).load(cfg["path"], limit=None)
                 if _in_sequence(t, cfg["sequence"])]
    # A SWE-Bench-CL sequence is one repo+version across all its tasks; resolve it from
    # the first task that carries both.
    repo = version = ""
    for t in seq_tasks:
        repo = (getattr(t, "repo", "") or "").strip()
        version = str((getattr(t, "metadata", None) or {}).get("version") or "").strip()
        if repo and version:
            break
    if not (repo and version):
        return
    print(f"  prewarming shared grading venv for sequence {cfg['sequence']} "
          f"({repo}@{version})…", flush=True)
    try:
        py = prewarm(repo, version)
    except Exception as exc:  # noqa: BLE001 - prewarm is best-effort; never abort the stage
        print(f"  venv prewarm skipped: {type(exc).__name__}: {str(exc)[:120]}",
              file=sys.stderr, flush=True)
        return
    if py:
        print(f"  ✓ shared venv ready ({py})", flush=True)
    else:
        print("  venv prewarm not available — falling back to per-task venvs", flush=True)


def _native_cl_for_stage(stage: str, cfg: dict, substrate: Path) -> Optional[dict]:
    """Compute the paper-native CL report for an eval stage and return its dict.

    NOTE: the native evaluator runs its OWN mem-on / re-test / mem-off A/B over the
    sequence, and assumes a per-sequence memory reset -- which the shared accumulating
    substrate deliberately breaks. The report is captured for comparison and flagged in
    the summary; it is not the pipeline's primary metric (resolve-rate accuracy is)."""
    from ..loaders import get_loader
    from ..native.registry import get_native_evaluator

    bench = Benchmark.from_str(cfg["benchmark"])
    tasks = [t for t in get_loader(bench).load(cfg["path"], limit=None)
             if _in_sequence(t, cfg["sequence"])]
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

    Wired to the SAME dreaming surface the plugin uses: build the RouterStore for the
    store dir (``cookbook_memory.core.contract.build_store`` via the
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


def _sandbox_auth_probe(config_dir: Path, *, model: str, timeout: int = 60) -> bool:
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
        proc = subprocess.run([exe, "-p", "ok", "--model", model], env=env, timeout=timeout,
                              capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    out = (proc.stdout + proc.stderr).lower()
    if proc.returncode != 0:
        return False
    return "not logged in" not in out and "/login" not in out and "invalid api key" not in out


def _ensure_cursor_ready(model: str) -> None:
    """Fail-closed readiness check for the Cursor harness (ADR-harness-013/014).

    Unlike Claude (one shared CLAUDE_CONFIG_DIR sandbox), the Cursor adapter builds a
    fresh ``HOME`` sandbox PER TURN and authenticates with ``CURSOR_API_KEY`` (no
    keychain, no interactive login — so no up-front sandbox build is needed). This
    check just verifies, before any stage runs, that (1) ``cursor-agent`` is installed
    and (2) an API key is configured — failing with an actionable message otherwise,
    mirroring the Claude path's fail-closed contract.

    Skipped when ``MEMEVAL_PIPELINE_SKIP_AUTH_PROBE`` is set (offline tests)."""
    import os

    from ..cursorcli import platform as cursor_platform
    from ..cursorcli import sandbox as cursor_sandbox

    if os.environ.get("MEMEVAL_PIPELINE_SKIP_AUTH_PROBE"):
        return  # offline tests: skip the binary/key probe

    rt = cursor_platform.detect()
    if rt is None:
        raise SystemExit(
            "\nThe Cursor CLI (cursor-agent) was not found — aborting before any stage "
            "runs.\nInstall it with `curl https://cursor.com/install -fsS | bash` "
            "(puts cursor-agent on PATH at ~/.local/bin), then re-run. Override the "
            "path with $CURSOR_AGENT_CLI.\n"
        )
    if cursor_sandbox.api_key() is None:
        raise SystemExit(
            "\nThe Cursor harness needs CURSOR_API_KEY — aborting before any stage "
            "runs.\nIt authenticates headlessly with a keychain-free API key so "
            "per-stage sandboxes can run in parallel (ADR-harness-014). Generate one "
            "at https://cursor.com/dashboard and set CURSOR_API_KEY in your .env "
            "(see .env.example) or export it, then re-run.\n"
        )
    print(f"cursor-agent: {rt.exe} · CURSOR_API_KEY set · model {model} — per-stage "
          f"HOME sandboxes, no host config touched.", flush=True)


def _ensure_sandbox_ready(model: str) -> None:
    """Make EVERY stage use the isolated sandbox CLAUDE_CONFIG_DIR — never the host — and
    FAIL CLOSED before running anything if it isn't authenticated.

    The harness sandboxes ``claude`` so a run never picks up the host's skills / agents /
    CLAUDE.md / auth. But ``active_config_dir()`` only returns the sandbox once it has been
    BUILT (its ``settings.json`` exists), and the sandbox was previously built lazily inside
    the first plugin-real stage — so a memoryless base stage ran against the HOST config.
    Build it up front here so the stage resolves to the sandbox, then PROBE the sandbox's
    auth with a real ``claude -p`` turn and abort if it's logged out (a file check alone is
    a false positive on macOS, where the token lives in the keychain, not on disk).

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
        sandbox.build(d)  # writes settings.json so active_config_dir() returns it
    # Ensure the cookbook-memory plugin's MCP tools are pre-approved in the sandbox
    # settings (idempotent; upgrades a sandbox built before this rule existed). This is
    # what lets the plugin-real stage run the SAME CLI as the no-plugin control — no
    # restrictive --allowedTools — so the only difference between them is memory.
    sandbox.ensure_plugin_tool_allowed(d)

    if os.environ.get("MEMEVAL_PIPELINE_SKIP_AUTH_PROBE"):
        return  # offline tests: skip the network probe

    print(f"sandbox: {d} — verifying login before any stage runs…", flush=True)
    if not _sandbox_auth_probe(d, model=model):
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
    """Run the single selected stage over one sequence and write results + summary.
    Returns the summary dict."""
    from ..cost import CostTracker
    from ..results import resolve_pipeline_version, run_timestamp
    from . import pipeline_summary as PS

    import time

    benchmark = cfg["benchmark"]
    stage = cfg["stage"]
    if cfg.get("harness", "claude") == "cursor":
        _ensure_cursor_ready(cfg["model"])  # cursor-agent + CURSOR_API_KEY, per-stage HOME sandboxes
    else:
        _ensure_sandbox_ready(cfg["model"])  # MUST be first — the stage uses the sandbox, never the host
    _warn_if_memory_cannot_accumulate()  # OPENROUTER_API_KEY gates daydream memory extraction
    version_info = resolve_pipeline_version(override=cfg.get("results_version"))
    version = version_info["version"]
    stamp = run_timestamp()
    results_root = Path(cfg["results_dir"])
    substrate = (results_root / version / "_memory").resolve()
    substrate.mkdir(parents=True, exist_ok=True)
    source_memory_info = _seed_source_memory(cfg, substrate)

    meta = _pipeline_meta(cfg, version_info, substrate, stamp)
    if source_memory_info:
        meta["source_memory"] = source_memory_info["path"]
        meta["source_memory_health"] = source_memory_info["health"]
        meta["source_memory_copied_to"] = source_memory_info["copied_to"]
    meta["started_at"] = time.time()
    print(f"pipeline v{version.lstrip('v')} · {benchmark} · sequence {cfg['sequence']} · "
          f"stage {stage} · limit {cfg['limit']} · harness {cfg.get('harness', 'claude')} · "
          f"model {cfg['model']}")
    print(f"shared memory substrate: {substrate}")
    if source_memory_info:
        print(f"source memory copied from: {source_memory_info['path']}")
    if version_info.get("untagged"):
        src = version_info.get("source")
        if src == "branch-commit":
            note = (f"NOTE: HEAD is untagged -> version keyed by branch "
                    f"'{version_info.get('branch')}' plus commit {version_info.get('git_sha')} "
                    f"({version}). Use --results-version to intentionally choose the output "
                    f"namespace; tag the commit for an archival, comparable run.")
        elif src == "override":
            note = (f"NOTE: HEAD is untagged -> using explicit reusable results version "
                    f"{version}.")
        else:
            note = ("NOTE: HEAD is untagged and detached/branchless -> version fell back to "
                    f"MEMORY_VERSION (v{MEMORY_VERSION}); tag the commit for a comparable run.")
        print(note, file=sys.stderr)

    rows: list[dict] = []
    native_by_stage: dict[str, dict] = {}
    dream: Optional[dict] = None
    in_progress: dict[str, Any] = {"stage": None, "row": None}

    def _write_partial() -> None:
        # The completed stage row + the in-progress partial row (if any), so the results
        # file exists from the first task and grows live as the stage runs.
        live = list(rows)
        if in_progress["row"] is not None:
            live.append(in_progress["row"])
        PS.write_pipeline_results(benchmark=benchmark, version=version, timestamp=stamp,
                                  rows=live, pipeline_meta=meta, dream=dream,
                                  root=str(results_root))

    def _on_task(stage_name: str, partial: Any) -> None:
        # After each task, refresh the stage's partial row and rewrite the results file
        # so progress is visible on disk immediately.
        in_progress["stage"] = stage_name
        in_progress["row"] = PS.stage_row(partial, stage=stage_name,
                                          stage_index=_STAGE_INDEX[stage_name], pipeline_meta=meta)
        _write_partial()

    active = [stage]
    meta["stages_run"] = active
    meta["preflight"] = _preflight(cfg, substrate, active)
    cost = CostTracker(budget_usd=cfg["budget_usd"]) if cfg["budget_usd"] and cfg["budget_usd"] > 0 else None
    total = _stage_task_total(cfg)
    meta["n_tasks"] = total
    nat = " + native-CL pass" if cfg["native_cl"] else ""
    dream_note = " (dream pass first)" if stage == "plugin-dreamed" else ""
    print(f"running stage {stage}{dream_note} × {total} tasks{nat} "
          f"(plugin stages run {cfg['plugin_workers']} at a time)…", flush=True)
    _write_partial()  # create the results file immediately (header + pipeline metadata)
    print(f"results file: {PS.benchmark_results_path(benchmark, version=version, timestamp=stamp, root=str(results_root))}",
          flush=True)

    # plugin-dreamed runs ONE whole-store consolidation pass over the substrate first, so
    # the stage reflects any dream mutations. The other stages run no dream pass.
    if stage == "plugin-dreamed":
        print("\n── dream ──────────", flush=True)
        print("  running whole-store dreaming consolidation…",
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

    rows.append(_run_one(stage, cfg, substrate, cost, native_by_stage, meta, total,
                         on_task=lambda p: _on_task(stage, p)))
    in_progress["row"] = None
    meta["ended_at"] = time.time()

    path = PS.write_pipeline_results(benchmark=benchmark, version=version, timestamp=stamp,
                                     rows=rows, pipeline_meta=meta, dream=dream,
                                     root=str(results_root))
    summary = PS.build_summary(benchmark=benchmark, rows=rows, pipeline_meta=meta,
                               dream=dream, native_by_stage=native_by_stage)
    md_path, json_path = PS.write_summary(benchmark=benchmark, version=version,
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
    # A --budget-usd cap is enforced via a shared CostTracker; its spent_usd is
    # cumulative across any pre-stage spend (e.g. the dream pass), so subtract the spend
    # at stage start to report only THIS stage's cost.
    cost_base = cost.spent_usd if cost is not None else 0.0
    memory_before = _store_health(substrate)
    print(f"\n── stage {stage} (mode={_STAGE_MODE[stage]}) · {total} tasks "
          f"──────────", flush=True)
    rr = _run_eval_stage(stage, cfg, substrate, cost=cost, total=total, on_task=on_task,
                         cost_base=cost_base)
    memory_after = _store_health(substrate)
    memory_delta = _health_delta(memory_before, memory_after)
    warnings = _stage_warnings(stage, cfg, rr, memory_before, memory_after, memory_delta)
    _rebase_cost(rr, cost_base)
    m = rr.metrics
    secs = int(time.monotonic() - t0)
    resolved = sum(1 for t in rr.trajectories if t.success is True)
    graded = sum(1 for t in rr.trajectories if t.success is not None)
    ungraded = rr.n_tasks - graded
    print(f"  ✓ stage {stage} done · {resolved}/{graded} graded resolved"
          f" · {ungraded}/{rr.n_tasks} ungraded · acc={m.accuracy:.3f} "
          f"rel={m.relevancy:.3f} eff={m.efficiency:.3f} · ${rr.cost_usd:.4f} · {secs}s",
          flush=True)
    if cfg["native_cl"]:
        print(f"  computing native CL metrics for stage {stage} "
              f"(mem-on / re-test / mem-off passes)…", flush=True)
        try:
            report = _native_cl_for_stage(stage, cfg, substrate)
            if report is not None:
                native_by_stage[stage] = report
                print(f"  ✓ native CL captured for stage {stage}", flush=True)
        except Exception as exc:  # native CL is supplementary -- never abort the stage
            print(f"  native CL skipped: {type(exc).__name__}: {str(exc)[:120]}",
                  file=sys.stderr, flush=True)
    return PS.stage_row(
        rr,
        stage=stage,
        stage_index=idx,
        pipeline_meta=meta,
        extra={
            "memory_health": {
                "before": memory_before,
                "after": memory_after,
                "delta": memory_delta,
            },
            "warnings": warnings,
        },
    )


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
        dream_txt = " (dream pass first)" if cfg["stage"] == "plugin-dreamed" else ""
        print("About to run one stage of the pipeline:")
        print(f"  benchmark {cfg['benchmark']}  ·  sequence {cfg['sequence']}  ·  {limit_txt}")
        print(f"  stage     {cfg['stage']}{dream_txt}")
        print(f"  version   {cfg['results_version']}")
        print(f"  model     {cfg['model']}  ·  grader {cfg['grader']}  ·  budget {budget_txt}")
        print(f"  native CL {'on' if cfg['native_cl'] else 'off'}")
        ans = _ask("run this stage now?", "y", choices=["y", "n"]).lower()
        if ans not in ("y", "yes"):
            print("aborted.")
            return 0

    run_pipeline(cfg)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
