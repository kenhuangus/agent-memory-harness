"""Native-eval result records + the :class:`NativeEvaluator` contract.

These dataclasses are the STABLE OUTPUT shape every per-benchmark evaluator
produces, and the :class:`NativeEvaluator` Protocol is the STABLE INPUT shape
the runner / CLI drive them through. Per-benchmark implementers code against
this file and only this file — keeping it small and frozen means their
evaluators stay drop-in.

Everything here is standard-library only and JSON-serializable.

The two-phase evaluator contract
--------------------------------
A native evaluator is split into two pure-ish phases so the expensive
agent/model loop is separate from the (deterministic) scoring:

1. :meth:`NativeEvaluator.run` — drive the agent/model over the tasks and
   return a list of :class:`PerTaskRecord` (one per scored trial), reusing
   :func:`memeval.agent.run_agent` / :class:`memeval.harness.InMemoryStore`
   wherever it makes sense. This is the only phase that touches a model.
2. :meth:`NativeEvaluator.score` — fold those records (joined back to their
   tasks) into a :class:`BenchmarkNativeReport`. Pure and deterministic; no
   model, no network. This is what the offline tests assert on directly.

Splitting them lets a caller cache ``run`` output (trajectories) and re-``score``
with a different judge / metric variant without re-running the agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Sequence, runtime_checkable

from ..schema import Task, Trajectory


# --------------------------------------------------------------------------- #
# Result records
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class NativeMetric:
    """One scalar metric exactly as the benchmark's paper reports it.

    Attributes
    ----------
    name:
        The paper's metric name (e.g. ``"qa_accuracy_overall"``,
        ``"line_f1"``, ``"resolve_rate"``, ``"forgetting"``).
    value:
        The metric value. Range depends on the metric — see ``better`` and the
        per-benchmark spec for the meaning/range.
    n:
        Sample size the value was computed over (number of tasks / trials /
        questions contributing). ``0`` means "no eligible items" — read
        ``value`` accordingly (usually ``0.0`` by convention).
    better:
        ``"higher"`` (default) or ``"lower"`` — the optimization direction, so a
        dashboard/aggregator knows whether more is good (accuracy) or bad
        (forgetting, redundancy, efficiency-overhead).
    metadata:
        Optional free-form provenance (formula notes, k, sub-counts).
    """

    name: str
    value: float
    n: int = 0
    better: str = "higher"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "n": self.n,
            "better": self.better,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ComponentScore:
    """A named stratum/slice (a benchmark "component") with its own metrics.

    A component is a reportable sub-population the paper breaks out — a
    LongMemEval question-type, a MemoryAgentBench competency, a ContextBench
    granularity (file/block/line) or language, a SWE-Bench-CL per-repo sequence,
    a SWE-ContextBench config (oracle/free × context/summary).

    ``metrics`` carries that slice's own :class:`NativeMetric` list (e.g. a
    per-type accuracy, or recall+precision+F1 for one granularity). ``n`` is the
    slice's task/trial count.
    """

    name: str
    metrics: list[NativeMetric] = field(default_factory=list)
    n: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def add(self, metric: NativeMetric) -> "ComponentScore":
        """Append a metric and return self (chainable)."""
        self.metrics.append(metric)
        return self

    def get(self, name: str) -> Optional[NativeMetric]:
        """Return the first metric named ``name`` in this slice, or ``None``."""
        for m in self.metrics:
            if m.name == name:
                return m
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "n": self.n,
            "metrics": [m.to_dict() for m in self.metrics],
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class BenchmarkNativeReport:
    """The full native-eval result for one benchmark + one run configuration.

    Attributes
    ----------
    benchmark:
        Benchmark id string (``Benchmark.value``, e.g. ``"longmemeval"``).
    mode:
        The memory/agent mode the run used (``"off"`` | ``"builtin"`` |
        ``"plugin"`` | ``"plugin-real"`` | ``"echo"``). Free-form so new modes
        slot in.
    n_tasks:
        Number of scored tasks/trials in this report.
    metrics:
        The headline (overall) :class:`NativeMetric` list — the paper's primary
        numbers (e.g. ``qa_accuracy_overall``, ``avg_line_f1``, ``resolve_rate``,
        the CL-Score suite).
    components:
        Mapping ``component_name -> ComponentScore`` for every reportable slice.
    metadata:
        Run provenance (source path, judge name, limit, paper sources, notes).
    """

    benchmark: str
    mode: str
    n_tasks: int = 0
    metrics: list[NativeMetric] = field(default_factory=list)
    components: dict[str, ComponentScore] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- builder helpers (optional sugar for evaluators) ------------------- #
    def add_metric(self, metric: NativeMetric) -> "BenchmarkNativeReport":
        """Append a headline metric and return self (chainable)."""
        self.metrics.append(metric)
        return self

    def add_component(self, comp: ComponentScore) -> "BenchmarkNativeReport":
        """Register a component slice by its name and return self (chainable)."""
        self.components[comp.name] = comp
        return self

    def metric(self, name: str) -> Optional[NativeMetric]:
        """Return the first headline metric named ``name``, or ``None``."""
        for m in self.metrics:
            if m.name == name:
                return m
        return None

    def to_dict(self) -> dict[str, Any]:
        """Fully JSON-serializable dict (what the CLI prints)."""
        return {
            "benchmark": self.benchmark,
            "mode": self.mode,
            "n_tasks": self.n_tasks,
            "metrics": [m.to_dict() for m in self.metrics],
            "components": {k: v.to_dict() for k, v in self.components.items()},
            "metadata": dict(self.metadata),
        }


# --------------------------------------------------------------------------- #
# Per-task intermediate record (the hand-off between run() and score())
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class PerTaskRecord:
    """One trial's intermediate result: the bridge from ``run`` to ``score``.

    An evaluator's :meth:`NativeEvaluator.run` returns a list of these; its
    :meth:`NativeEvaluator.score` consumes them (joined to the tasks). The
    record carries the full :class:`~memeval.schema.Trajectory` (so the scorer
    can read retrieve steps, prediction, success, tokens) plus convenience
    fields lifted out of it.

    ``task_id`` keys the record back to its :class:`~memeval.schema.Task`.
    ``prediction`` and ``success`` mirror the trajectory's fields for ergonomics.
    ``memory_on`` records which A/B condition produced it (used by metrics that
    contrast memory-on vs memory-off, e.g. SWE-Bench-CL Forward Transfer and
    SWE-ContextBench context-lift). ``extra`` is free-form per-trial scratch
    (e.g. a per-question judge label cached by run()).
    """

    task_id: str
    trajectory: Trajectory
    prediction: str = ""
    success: Optional[bool] = None
    memory_on: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_trajectory(cls, traj: Trajectory, **extra: Any) -> "PerTaskRecord":
        """Build a record from a finished :class:`~memeval.schema.Trajectory`."""
        return cls(
            task_id=traj.task_id,
            trajectory=traj,
            prediction=traj.prediction or "",
            success=traj.success,
            memory_on=traj.memory_on,
            extra=dict(extra),
        )


# --------------------------------------------------------------------------- #
# The NativeEvaluator contract
# --------------------------------------------------------------------------- #
# Forward-declared so the Protocol signatures can name the Judge without a hard
# import cycle (judge.py imports nothing from spec.py, so this is purely a type).
@runtime_checkable
class _JudgeLike(Protocol):  # pragma: no cover - typing structural shape only
    def judge(
        self, question: str, gold: str, prediction: str, *, kind: str = ...
    ) -> Any: ...


@runtime_checkable
class NativeEvaluator(Protocol):
    """The two-phase contract every per-benchmark evaluator implements.

    Implementers create ``memeval/native/evaluators/<benchmark>.py`` with a class
    satisfying this Protocol and register it (see
    :func:`memeval.native.registry.register_native_evaluator`). They may subclass
    :class:`memeval.native.evaluators.base.BaseNativeEvaluator` for the common
    plumbing, but only this structural shape is required.

    Attributes
    ----------
    benchmark:
        The :class:`~memeval.schema.Benchmark` value string this evaluator scores
        (e.g. ``"longmemeval"``). Used by the registry / reports.
    """

    benchmark: str

    def run(
        self,
        tasks: Sequence[Task],
        *,
        agent_or_model: Any,
        mode: str,
        store: Any = None,
        judge: Optional[_JudgeLike] = None,
        cost: Any = None,
        limit: Optional[int] = None,
        **kwargs: Any,
    ) -> list[PerTaskRecord]:
        """Drive the agent/model over ``tasks`` → per-task intermediate records.

        Parameters
        ----------
        tasks:
            The already-loaded, already-limited :class:`~memeval.schema.Task`
            list (the runner loads + limits via the existing loader so the
            evaluator does not re-load).
        agent_or_model:
            Either an :class:`memeval.agent.AgentAdapter` (multi-step; drive via
            :func:`memeval.agent.run_agent`) or a
            :class:`memeval.protocols.ModelAdapter` (single-shot; drive via
            :func:`memeval.harness.run`). The offline default is
            :class:`memeval.agent.EchoAgent` / :class:`memeval.models.EchoModel`.
        mode:
            Memory mode string (``"off"`` | ``"builtin"`` | ``"plugin"`` |
            ``"plugin-real"`` | ``"echo"``). The evaluator maps it to a
            ``memory`` bool (and, for continual benches, the A/B passes it needs).
        store:
            Shared :class:`memeval.protocols.MemoryStore` (the team's memory
            harness in real runs); defaults to per-task/per-group
            :class:`memeval.harness.InMemoryStore` when ``None``.
        judge:
            A :class:`memeval.native.judge.Judge`. The offline default is
            :class:`~memeval.native.judge.DeterministicJudge`. Only the
            judge-needing benchmark (LongMemEval) consults it; others ignore it.
        cost:
            Optional :class:`memeval.cost.CostTracker` for budget enforcement.
        limit:
            Already applied by the runner; accepted for symmetry / re-entrancy.
        **kwargs:
            Evaluator-specific knobs (e.g. ``chunk_tokens`` for MemoryAgentBench,
            ``k`` retrieval depth, ``granularities`` for ContextBench).

        Returns
        -------
        list[PerTaskRecord]
            One record per scored trial. For A/B benches (SWE-Bench-CL FWT,
            SWE-ContextBench lift) return BOTH the memory-on and memory-off
            records, distinguished by :attr:`PerTaskRecord.memory_on`.
        """
        ...

    def score(
        self,
        records: Sequence[PerTaskRecord],
        tasks: Sequence[Task],
    ) -> BenchmarkNativeReport:
        """Fold per-task records (joined to ``tasks``) → a native report.

        Pure + deterministic: no model, no network, no wall-clock. Reads the
        trajectories' retrieve steps / predictions / successes and the tasks'
        gold fields, applies the paper's formulas, and returns the populated
        :class:`BenchmarkNativeReport` (headline ``metrics`` + per-slice
        ``components``). This is exactly what the offline tests assert on.
        """
        ...


# A plain callable grader type, mirroring memeval.grader.Grader, re-exported for
# evaluators that want to type their offline CODE grader argument.
OfflineGrader = Callable[[Task, str], Optional[bool]]


__all__ = [
    "NativeMetric",
    "ComponentScore",
    "BenchmarkNativeReport",
    "PerTaskRecord",
    "NativeEvaluator",
    "OfflineGrader",
]
