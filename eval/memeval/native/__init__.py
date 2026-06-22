"""Benchmark-NATIVE evaluation package (additive; stdlib-only offline).

The existing :mod:`memeval` harness scores every benchmark with ONE shared set
of memory metrics (recency / efficiency / relevancy / accuracy). That is the
right cross-benchmark *comparison*, but it is **not** how each cited paper
actually reports its own numbers. This package adds, alongside the frozen
contract, a way to score each benchmark **the way its paper does** — LongMemEval
QA accuracy by question-type + abstention, MemoryAgentBench SubEM/EM per
competency, ContextBench file/block/line recall-precision-F1 + process metrics,
SWE-ContextBench resolve-rate / context-lift / match-rate, and SWE-Bench-CL's
continual-learning suite (ACC / Forgetting / BWT / FWT / AULC / CL-Score).

Design rules (load-bearing)
---------------------------
* **Additive only.** Nothing here edits the frozen schema/protocols/metrics or
  the existing pipeline. It REUSES them by import: :class:`memeval.schema.Task`
  /:class:`~memeval.schema.Trajectory`, :func:`memeval.agent.run_agent`,
  :class:`memeval.agent.EchoAgent`, :class:`memeval.models.EchoModel`,
  :class:`memeval.harness.InMemoryStore`, :func:`memeval.loaders.get_loader`,
  and :mod:`memeval.grader` for CODE resolve.
* **Offline path is stdlib-only and importable with no heavy deps.** Anthropic /
  datasets / numpy stay lazy. The offline run uses EchoAgent / EchoModel +
  InMemoryStore + :class:`~memeval.native.judge.DeterministicJudge` — no network,
  no LLM.
* **Accuracy over cleverness.** Each evaluator's run protocol and metrics match
  the paper, not memory.

Public surface
--------------
* :class:`~memeval.native.spec.BenchmarkNativeReport`,
  :class:`~memeval.native.spec.NativeMetric`,
  :class:`~memeval.native.spec.ComponentScore` — the result records.
* :class:`~memeval.native.spec.NativeEvaluator` — the Protocol each per-benchmark
  evaluator implements (``run`` then ``score``).
* :func:`~memeval.native.runner.run_native` — resolve loader + evaluator and run.
* :func:`~memeval.native.registry.get_native_evaluator` /
  :func:`~memeval.native.registry.register_native_evaluator` — the plug-in point
  the per-benchmark implementers register through.
* :class:`~memeval.native.judge.DeterministicJudge` /
  :class:`~memeval.native.judge.AnthropicJudge` — the pluggable judge.
"""

from __future__ import annotations

from .spec import (
    BenchmarkNativeReport,
    ComponentScore,
    NativeEvaluator,
    NativeMetric,
    PerTaskRecord,
)
from .judge import DeterministicJudge, Judge, get_judge
from .registry import (
    get_native_evaluator,
    register_native_evaluator,
    available_native,
)
from .runner import run_native

__all__ = [
    "BenchmarkNativeReport",
    "ComponentScore",
    "NativeMetric",
    "NativeEvaluator",
    "PerTaskRecord",
    "Judge",
    "DeterministicJudge",
    "get_judge",
    "get_native_evaluator",
    "register_native_evaluator",
    "available_native",
    "run_native",
]
