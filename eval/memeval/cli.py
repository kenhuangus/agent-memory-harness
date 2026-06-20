"""Command-line entry point: ``python -m memeval.cli run --benchmark ...``.

Thin argparse wrapper over :func:`memeval.harness.run`. The default model is
``echo`` so the CLI runs fully offline (no network, no extra deps) against a
local fixture; pass ``--model claude-haiku-4-5`` (etc.) and a real source to go
online. Output is the :meth:`RunResult.to_dict` JSON summary on stdout, with an
optional ``--out`` JSONL trajectory log written alongside.

Examples
--------
Offline smoke run over a fixture::

    python -m memeval.cli run --benchmark longmemeval \
        --path tests/fixtures/longmemeval.json --memory

Budgeted online run for a captain's key::

    python -m memeval.cli run --benchmark swe_bench_cl --model claude-haiku-4-5 \
        --keys memeval/config/keys.example.json --captain swe_bench_cl \
        --memory --out runs/swe_cl.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from .cost import DEFAULT_BUDGET_USD, CostTracker, load_key_config
from .harness import run
from .models import get_model
from .schema import Benchmark


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with the ``run`` subcommand."""
    parser = argparse.ArgumentParser(
        prog="memeval",
        description="Evaluation harness for the AI Agent Memory Harness.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser(
        "run", help="Run one benchmark through one model+memory configuration."
    )
    run_p.add_argument(
        "--benchmark",
        required=True,
        help="Benchmark id (memoryagentbench | longmemeval | swe_contextbench "
        "| swe_bench_cl; loose forms accepted).",
    )
    run_p.add_argument(
        "--model",
        default="echo",
        help="Model id (default 'echo' = offline deterministic adapter).",
    )

    mem = run_p.add_mutually_exclusive_group()
    mem.add_argument(
        "--memory",
        dest="memory",
        action="store_true",
        help="Consult the memory store (memory-ON run).",
    )
    mem.add_argument(
        "--no-memory",
        dest="memory",
        action="store_false",
        help="Skip the memory store (memory-OFF baseline).",
    )
    run_p.set_defaults(memory=False)

    run_p.add_argument("--limit", type=int, default=None, help="Cap number of tasks.")
    run_p.add_argument(
        "--dev-slice",
        type=float,
        default=None,
        help="Stratified dev sample: fraction in (0,1] or an absolute count >1.",
    )
    run_p.add_argument(
        "--path",
        dest="path_or_id",
        default=None,
        help="Local fixture path or remote dataset id (defaults to loader source).",
    )
    run_p.add_argument(
        "--k", type=int, default=5, help="Retrieval depth (top-k memories)."
    )
    run_p.add_argument(
        "--budget-usd",
        type=float,
        default=None,
        help=f"Abort if spend exceeds this (default ${DEFAULT_BUDGET_USD:.0f}; <=0 means no cap).",
    )
    run_p.add_argument(
        "--budget-tokens", type=int, default=None, help="Abort if tokens exceed this."
    )
    run_p.add_argument(
        "--out",
        default=None,
        help="Write per-task trajectories to this JSONL file.",
    )
    run_p.add_argument(
        "--keys",
        default=None,
        help="Path to a keys config JSON (config/keys.example.json shape).",
    )
    run_p.add_argument(
        "--captain",
        default=None,
        help="Captain/benchmark key inside --keys to source budget + api_key_env.",
    )
    run_p.add_argument(
        "--tau", type=float, default=86400.0, help="Recency decay constant (s)."
    )
    run_p.add_argument(
        "--threshold",
        type=float,
        default=0.7,
        help="Relevancy precision@k score threshold.",
    )
    run_p.add_argument(
        "--indent", type=int, default=2, help="JSON indent for stdout summary."
    )
    return parser


def _resolve_key_config(
    keys_path: Optional[str], captain: Optional[str]
) -> dict:
    """Load the captain's entry from a keys config, or ``{}`` if not requested."""
    if not keys_path:
        return {}
    cfg = load_key_config(keys_path)
    if captain:
        if captain not in cfg:
            raise SystemExit(
                f"captain/benchmark {captain!r} not in {keys_path} "
                f"(have: {', '.join(sorted(cfg))})"
            )
        return cfg[captain]
    return {}


def main(argv: Optional[list[str]] = None) -> int:
    """Parse args and execute. Returns a process exit code (0 == success)."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "run":  # argparse 'required=True' guards this; belt+braces
        parser.print_help()
        return 2

    bench = Benchmark.from_str(args.benchmark)

    # Captain config can supply budget + the env var holding the API key.
    key_cfg = _resolve_key_config(args.keys, args.captain)
    budget_usd = args.budget_usd
    budget_tokens = args.budget_tokens
    if budget_usd is None and "budget_usd" in key_cfg:
        budget_usd = float(key_cfg["budget_usd"])
    if budget_usd is None:
        budget_usd = DEFAULT_BUDGET_USD  # default cap when nothing supplied
    if budget_usd <= 0:
        budget_usd = None  # explicit opt-out: <=0 means no cap
    if budget_tokens is None and "budget_tokens" in key_cfg:
        budget_tokens = int(key_cfg["budget_tokens"])

    # Resolve the model. For non-echo models, route the captain's api_key_env.
    model_kwargs: dict = {}
    api_key_env = key_cfg.get("api_key_env")
    if api_key_env and args.model.strip().lower() not in {"echo", "none", ""}:
        model_kwargs["api_key_env"] = api_key_env
        if not os.environ.get(api_key_env):
            print(
                f"warning: api_key_env {api_key_env!r} is unset in the environment",
                file=sys.stderr,
            )
    model = get_model(args.model, **model_kwargs)

    cost = None
    if budget_usd is not None or budget_tokens is not None:
        cost = CostTracker(budget_usd=budget_usd, budget_tokens=budget_tokens)

    logger = None
    if args.out:
        from .trajectory import TrajectoryLogger

        logger = TrajectoryLogger(args.out, append=False)

    try:
        result = run(
            bench,
            model,
            args.memory,
            limit=args.limit,
            cost=cost,
            logger=logger,
            dev_slice=args.dev_slice,
            path_or_id=args.path_or_id,
            tau=args.tau,
            threshold=args.threshold,
            k=args.k,
        )
    finally:
        if logger is not None:
            logger.close()

    json.dump(result.to_dict(), sys.stdout, indent=args.indent, sort_keys=True)
    sys.stdout.write("\n")

    # Non-zero exit when a budget abort produced only a partial run, so a
    # sweep script can detect the overrun from the exit code.
    return 1 if result.budget_exceeded else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
