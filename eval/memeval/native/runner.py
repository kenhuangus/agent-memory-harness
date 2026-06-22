"""``run_native`` — load a benchmark, resolve its native evaluator, run + score.

This is the single offline-safe entry point that ties the native package
together, mirroring :func:`memeval.harness.run` but producing a
:class:`~memeval.native.spec.BenchmarkNativeReport` (paper-native metrics)
instead of the shared four-metric :class:`~memeval.schema.RunResult`.

Flow
----
1. Resolve the benchmark (:meth:`memeval.schema.Benchmark.from_str`).
2. Load its tasks with the EXISTING loader (:func:`memeval.loaders.get_loader`),
   honoring ``limit`` — so the native package never re-implements loading.
3. Resolve the evaluator (:func:`memeval.native.registry.get_native_evaluator`).
4. Resolve the agent/model (offline default: a fresh
   :class:`memeval.agent.EchoAgent`) and the judge (offline default:
   :class:`memeval.native.judge.DeterministicJudge`).
5. ``records = evaluator.run(tasks, ...)`` then
   ``report = evaluator.score(records, tasks)``.

Fully offline by default: EchoAgent + EchoModel + per-task/per-group
:class:`memeval.harness.InMemoryStore` + DeterministicJudge → no network, no LLM,
stdlib only.
"""

from __future__ import annotations

from typing import Any, Optional

from ..loaders import get_loader
from ..schema import Benchmark
from .judge import Judge, get_judge
from .registry import get_native_evaluator
from .spec import BenchmarkNativeReport


def run_native(
    benchmark: "Benchmark | str",
    *,
    model_or_agent: Any = None,
    mode: str = "off",
    path_or_id: Optional[str] = None,
    store: Any = None,
    limit: Optional[int] = None,
    judge: "str | Judge | None" = None,
    cost: Any = None,
    **evaluator_kwargs: Any,
) -> BenchmarkNativeReport:
    """Run one benchmark's NATIVE evaluation and return its report.

    Parameters
    ----------
    benchmark:
        Benchmark enum or loose string (``"longmemeval"``, ``"swe-bench-cl"``…).
    model_or_agent:
        An :class:`memeval.agent.AgentAdapter` (multi-step) or a
        :class:`memeval.protocols.ModelAdapter` (single-shot), or ``None`` for
        the offline default (:class:`memeval.agent.EchoAgent` over
        :class:`memeval.models.EchoModel`). The evaluator's ``run`` wraps a bare
        model in an EchoAgent as needed.
    mode:
        Memory/agent mode (``"off"`` | ``"builtin"`` | ``"plugin"`` |
        ``"plugin-real"`` | ``"echo"``). ``"off"`` is the memory-OFF baseline;
        the rest enable memory. The concrete memory mechanism is the agent's
        responsibility (offline EchoAgent; online ClaudeCodeAgent).
    path_or_id:
        Local fixture path or remote dataset id passed to the loader (defaults to
        the loader's own default source).
    store:
        Shared :class:`memeval.protocols.MemoryStore` (the team's memory harness
        in real runs). ``None`` → per-task/per-group
        :class:`memeval.harness.InMemoryStore`.
    limit:
        Cap the number of tasks (applied at load time).
    judge:
        Judge spec — ``None`` / ``"deterministic"`` → offline
        :class:`memeval.native.judge.DeterministicJudge`; a model id →
        :class:`~memeval.native.judge.AnthropicJudge` (lazy ``anthropic``). Only
        LongMemEval consults it.
    cost:
        Optional :class:`memeval.cost.CostTracker` for budget enforcement.
    **evaluator_kwargs:
        Forwarded to ``evaluator.run`` (e.g. ``chunk_tokens``, ``k``,
        ``granularities``).

    Returns
    -------
    BenchmarkNativeReport
        Paper-native headline metrics + per-component breakdowns.
    """
    bench = benchmark if isinstance(benchmark, Benchmark) else Benchmark.from_str(benchmark)

    loader = get_loader(bench)
    tasks = loader.load(path_or_id, limit=limit)

    evaluator = get_native_evaluator(bench)
    j = get_judge(judge)

    records = evaluator.run(
        tasks,
        agent_or_model=model_or_agent,
        mode=mode,
        store=store,
        judge=j,
        cost=cost,
        limit=limit,
        **evaluator_kwargs,
    )
    report = evaluator.score(records, tasks)

    # Stamp run provenance the evaluator may not have set itself (non-destructive:
    # only fills gaps).
    report.metadata.setdefault("source", path_or_id or getattr(loader, "default_source", ""))
    report.metadata.setdefault("judge", getattr(j, "name", "deterministic"))
    report.metadata.setdefault("limit", limit)
    if not report.mode:
        report.mode = mode
    return report


__all__ = ["run_native"]
