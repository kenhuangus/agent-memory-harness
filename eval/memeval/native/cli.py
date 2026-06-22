"""CLI entry point: ``python -m memeval.native.cli run --benchmark ...``.

A NEW, additive command line for the benchmark-NATIVE evaluation, mirroring the
style of :mod:`memeval.cli` (which it does NOT modify). Defaults run fully
offline: ``--model echo`` + ``--mode off`` + the deterministic judge over a
local fixture, with no network and no extra deps. Output is the
:meth:`BenchmarkNativeReport.to_dict` JSON on stdout.

Examples
--------
Offline LongMemEval native QA-accuracy run over a fixture::

    python -m memeval.native.cli run --benchmark longmemeval \
        --mode echo --path tests/fixtures/longmemeval.json

Offline SWE-Bench-CL continual-learning suite::

    python -m memeval.native.cli run --benchmark swe_bench_cl \
        --mode echo --path tests/fixtures/swe_bench_cl.json --limit 50

Online (paid) LongMemEval with the real LLM judge::

    python -m memeval.native.cli run --benchmark longmemeval \
        --model claude-haiku-4-5 --mode plugin --judge claude-sonnet-4-5 \
        --path longmemeval_s
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from ..models import get_model
from ..schema import Benchmark
from .judge import get_judge
from .runner import run_native

#: Memory/agent modes the runner understands (see ``base.mode_to_memory``).
_MODES = ("off", "builtin", "plugin", "plugin-real", "echo")


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with the ``run`` subcommand."""
    parser = argparse.ArgumentParser(
        prog="memeval-native",
        description="Benchmark-native evaluation for the AI Agent Memory Harness "
        "(paper-faithful metrics, additive to the shared four-metric harness).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser(
        "run", help="Run one benchmark's native evaluation and print its report."
    )
    run_p.add_argument(
        "--benchmark",
        required=True,
        help="Benchmark id (longmemeval | memoryagentbench | contextbench | "
        "swe_contextbench | swe_bench_cl; loose forms accepted).",
    )
    run_p.add_argument(
        "--model",
        default="echo",
        help="Model id (default 'echo' = offline deterministic adapter). A real "
        "id (e.g. claude-haiku-4-5) is wrapped in an EchoAgent for the loop.",
    )
    run_p.add_argument(
        "--mode",
        default="off",
        choices=_MODES,
        help="Memory mode: off (baseline) | builtin | plugin | plugin-real | echo.",
    )
    run_p.add_argument(
        "--path",
        dest="path_or_id",
        default=None,
        help="Local fixture path or remote dataset id (defaults to loader source).",
    )
    run_p.add_argument("--limit", type=int, default=None, help="Cap number of tasks.")
    run_p.add_argument(
        "--judge",
        default=None,
        help="Judge spec: 'deterministic' (default, offline) or a model id "
        "(e.g. claude-sonnet-4-5) for the live LLM judge. Only LongMemEval uses it.",
    )
    run_p.add_argument(
        "--k", type=int, default=5, help="Retrieval depth (top-k) for evaluators that use it."
    )
    run_p.add_argument(
        "--chunk-tokens",
        type=int,
        default=None,
        help="MemoryAgentBench chunk size (512 or 4096 in the paper); ignored elsewhere.",
    )
    run_p.add_argument(
        "--indent", type=int, default=2, help="JSON indent for stdout report."
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Parse args and execute. Returns a process exit code (0 == success)."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "run":  # argparse 'required=True' guards this; belt+braces
        parser.print_help()
        return 2

    bench = Benchmark.from_str(args.benchmark)

    # Resolve model (offline echo by default; a real id lazy-imports anthropic).
    model = get_model(args.model)
    # Resolve judge (offline deterministic by default).
    judge = get_judge(args.judge)
    if args.judge and args.judge.strip().lower() not in {"deterministic", "det", "offline", "echo", "none", ""}:
        # Live judge requested: warn if the key env is unset (don't fail here).
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print(
                "warning: --judge requests a live LLM judge but ANTHROPIC_API_KEY is unset",
                file=sys.stderr,
            )

    # Forward only the evaluator kwargs that were explicitly supplied, so each
    # evaluator falls back to its own defaults otherwise.
    evaluator_kwargs: dict = {"k": args.k}
    if args.chunk_tokens is not None:
        evaluator_kwargs["chunk_tokens"] = args.chunk_tokens

    report = run_native(
        bench,
        model_or_agent=model,
        mode=args.mode,
        path_or_id=args.path_or_id,
        limit=args.limit,
        judge=judge,
        **evaluator_kwargs,
    )

    json.dump(report.to_dict(), sys.stdout, indent=args.indent, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
