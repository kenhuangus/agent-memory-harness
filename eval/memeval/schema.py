"""Unified data model for the AI Agent Memory Harness evaluation infra.

This module is the FROZEN contract. Every other module (loaders, harness,
metrics, trajectory, cost, models) imports its types from here and from
``memeval.protocols``. Nothing here imports a third-party package -- it is
standard-library only and must import cleanly on Python 3.11+ (target 3.13).

The model is intentionally benchmark-agnostic: the four public benchmarks
(MemoryAgentBench, LongMemEval, SWE-ContextBench, SWE-Bench-CL) all normalize
into the same :class:`Task` shape so a single :func:`memeval.harness.run` can
drive them all.

Glossary
--------
Session       One timestamped chunk of conversation/history a memory may come from.
Task          One scorable unit: a question (QA) or a coding problem (CODE).
MemoryItem    Something written into a MemoryStore (the unit of persistence).
RetrievedItem A MemoryItem returned by a search, plus its score and rank.
TrajectoryStep One recorded action during a task run (retrieve / generate / etc.).
Trajectory    The ordered list of steps for one task -- the reproducibility log.
ModelConfig   Identity + price + memory-on/off flag for one evaluated configuration.
Metrics       The four numbers (recency, efficiency, relevancy, accuracy).
RunResult     Everything produced by one harness run over one benchmark+config.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class Benchmark(str, Enum):
    """The four public memory benchmarks this harness evaluates.

    Inherits from ``str`` so the enum value is JSON-serializable and usable
    directly as a dict key / CLI argument (``Benchmark("longmemeval")``).
    """

    MEMORY_AGENT_BENCH = "memoryagentbench"
    LONGMEMEVAL = "longmemeval"
    SWE_CONTEXTBENCH = "swe_contextbench"
    SWE_BENCH_CL = "swe_bench_cl"

    @classmethod
    def from_str(cls, value: str) -> "Benchmark":
        """Parse a loose string (case/sep-insensitive) into a Benchmark.

        Accepts e.g. ``"LongMemEval"``, ``"swe-bench-cl"``, ``"swe bench cl"``.
        """
        norm = value.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "memoryagentbench": cls.MEMORY_AGENT_BENCH,
            "memory_agent_bench": cls.MEMORY_AGENT_BENCH,
            "mab": cls.MEMORY_AGENT_BENCH,
            "longmemeval": cls.LONGMEMEVAL,
            "long_mem_eval": cls.LONGMEMEVAL,
            "lme": cls.LONGMEMEVAL,
            "swe_contextbench": cls.SWE_CONTEXTBENCH,
            "swe_context_bench": cls.SWE_CONTEXTBENCH,
            "scb": cls.SWE_CONTEXTBENCH,
            "swe_bench_cl": cls.SWE_BENCH_CL,
            "swebench_cl": cls.SWE_BENCH_CL,
            "swe_bench_continual": cls.SWE_BENCH_CL,
        }
        if norm in aliases:
            return aliases[norm]
        # Fall back to direct value match.
        return cls(norm)


class TaskKind(str, Enum):
    """Whether a task is graded as a question-answer or a code patch."""

    QA = "qa"
    CODE = "code"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _new_id(prefix: str) -> str:
    """Return a short, stable-format unique id like ``mem_3f2a1b9c``.

    Deterministic in *format* only; the value is random. Logic that must be
    reproducible should pass ids explicitly rather than relying on this.
    """
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- #
# Core data records
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Session:
    """One timestamped slice of prior interaction a memory can be drawn from.

    LongMemEval questions ship many sessions per question; MemoryAgentBench
    bundles a long history; SWE benchmarks may treat each prior issue as a
    session. ``timestamp`` is a Unix epoch float (UTC); ``index`` preserves
    original chronological order even when timestamps tie or are absent.
    """

    session_id: str
    content: str
    timestamp: float = 0.0
    index: int = 0
    role: str = "user"  # "user" | "assistant" | "system" | "tool"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Task:
    """One scorable unit, normalized across all four benchmarks.

    QA tasks use ``question`` + ``answer`` (gold). CODE tasks use ``question``
    (problem statement / issue text), ``repo``, ``base_commit`` and a gold
    ``patch``; success = the patch resolves / tests pass (graded externally).

    ``group_id`` carries the benchmark's grouping key -- ``group_id`` in
    SWE-ContextBench (shared-context tasks) and the *sequence* id in
    SWE-Bench-CL (chronological per-repo continual-learning order). ``order``
    is the within-group position used to preserve continual-learning ordering.

    ``gold_memory_ids`` lists the ids of the sessions/memories that *should*
    be retrieved to answer this task; the metrics use it to score recency and
    relevancy when ground-truth relevance is available.
    """

    task_id: str
    benchmark: Benchmark
    kind: TaskKind
    question: str
    answer: Optional[str] = None
    choices: Optional[list[str]] = None  # for multiple-choice QA
    sessions: list[Session] = field(default_factory=list)
    gold_memory_ids: list[str] = field(default_factory=list)
    # Grouping / ordering (SWE-ContextBench group_id, SWE-Bench-CL sequence).
    group_id: Optional[str] = None
    order: int = 0
    # CODE-task fields (None for QA).
    repo: Optional[str] = None
    base_commit: Optional[str] = None
    patch: Optional[str] = None
    test_patch: Optional[str] = None
    fail_to_pass: list[str] = field(default_factory=list)
    pass_to_pass: list[str] = field(default_factory=list)
    # Benchmark competency/ability label (e.g. "temporal_reasoning",
    # "conflict_resolution", "test_time_learning"); used for stratified slices.
    competency: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def stratum(self) -> str:
        """Return the key used to stratify dev slices: competency or kind."""
        return self.competency or self.kind.value


@dataclass(slots=True)
class MemoryItem:
    """A unit written into a MemoryStore (the persistence atom).

    Mirrors the harness's write-path tagging: ``timestamp`` (when learned),
    ``relevancy`` (write-time confidence/importance in [0,1]), ``session_id``
    (provenance session), ``source`` (provenance system), and ``embedding``
    (optional dense vector; ``None`` keeps the offline path numpy-free).
    ``tokens`` is the item's token cost, used by the efficiency metric.
    """

    item_id: str
    content: str
    timestamp: float = 0.0
    relevancy: float = 1.0
    session_id: Optional[str] = None
    source: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    embedding: Optional[list[float]] = None
    tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_session(cls, session: Session, **overrides: Any) -> "MemoryItem":
        """Build a MemoryItem from a Session, copying provenance fields."""
        base = dict(
            item_id=session.session_id,
            content=session.content,
            timestamp=session.timestamp,
            session_id=session.session_id,
            source="session",
            metadata=dict(session.metadata),
        )
        base.update(overrides)
        return cls(**base)


@dataclass(slots=True)
class RetrievedItem:
    """A MemoryItem returned by ``MemoryStore.search``, plus score and rank.

    ``score`` is the retriever's similarity/relevance score (cosine in [0,1]
    by convention, but any monotonic score is accepted). ``rank`` is 0-based
    (0 == top hit). ``is_gold`` is set by the metrics layer when the item's id
    is in the task's ``gold_memory_ids`` -- it is *not* required at search time.
    """

    item: MemoryItem
    score: float
    rank: int = 0
    is_gold: bool = False

    @property
    def item_id(self) -> str:
        return self.item.item_id

    @property
    def timestamp(self) -> float:
        return self.item.timestamp

    @property
    def tokens(self) -> int:
        return self.item.tokens


@dataclass(slots=True)
class TrajectoryStep:
    """One recorded action during a task run -- the reproducibility atom.

    ``kind`` is one of: ``"retrieve"``, ``"generate"``, ``"write"``,
    ``"judge"``, ``"error"``, ``"note"``. ``content`` holds the human-readable
    payload (prompt, completion, etc.). ``retrieved`` is populated on retrieve
    steps; ``tokens_in``/``tokens_out`` on generate steps. ``timestamp`` is the
    explicit time passed in by the caller (deterministic; never wall-clock in
    core logic). The dreaming worker consumes these via the JSONL reader.
    """

    step: int
    kind: str
    content: str = ""
    timestamp: float = 0.0
    retrieved: list[RetrievedItem] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Trajectory:
    """The ordered steps for one task -- one JSONL record per trajectory.

    Self-contained so it can be read back by the dreaming worker without the
    original benchmark. ``success`` and ``prediction`` are filled once the task
    is graded; ``memory_on`` records whether the run consulted memory.
    """

    task_id: str
    benchmark: Benchmark
    model: str
    memory_on: bool = False
    steps: list[TrajectoryStep] = field(default_factory=list)
    prediction: Optional[str] = None
    success: Optional[bool] = None
    started_at: float = 0.0
    ended_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def add(self, step: TrajectoryStep) -> TrajectoryStep:
        """Append a step (renumbering it to the current length) and return it."""
        step.step = len(self.steps)
        self.steps.append(step)
        return step

    @property
    def memory_tokens(self) -> int:
        """Total tokens contributed by retrieved memory across all steps."""
        return sum(
            r.tokens for s in self.steps for r in s.retrieved
        )

    @property
    def total_tokens(self) -> int:
        """Total prompt+completion tokens across all generate steps."""
        return sum(s.tokens_in + s.tokens_out for s in self.steps)


@dataclass(slots=True)
class ModelConfig:
    """Identity + price + memory flag for one evaluated configuration.

    ``price_in``/``price_out`` are USD per *million* tokens (matching
    ``cost.PRICING``). ``memory`` toggles the harness memory path. ``tier`` is
    used by cheapest-first ordering ("haiku" < "sonnet" < "opus"). ``label``
    is the human-readable cell name on the results dashboard, e.g.
    "haiku+mem", "opus(no-mem)".
    """

    name: str
    memory: bool = False
    price_in: float = 0.0
    price_out: float = 0.0
    tier: str = ""
    temperature: float = 0.0
    max_tokens: int = 1024
    label: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.label:
            self.label = f"{self.name}{'+mem' if self.memory else ''}"


@dataclass(slots=True)
class Metrics:
    """The four evaluation metrics. Higher is better for all except efficiency.

    recency
        Of queries whose gold-relevant memory is the freshest among the
        retrieved items, the fraction where that freshest relevant item is
        ranked #1 (rank == 0). Reported alongside a decayed variant
        ``mean(exp(-dt / tau))`` over each query's freshest relevant item,
        where ``dt`` is (query_time - item_time). Range [0, 1]; higher better.

    efficiency
        Memory-token overhead ratio: ``memory_tokens / total_tokens`` per
        retrieval, averaged over tasks. Target < ~0.10. Range [0, inf);
        LOWER is better.

    relevancy
        Mean similarity (cosine, or a provided score) of retrieved items vs.
        the query, plus precision@k = fraction of retrieved items scoring at
        or above ``threshold`` (default 0.7). Reported value is the mean
        similarity. Range [0, 1]; higher better.

    accuracy
        Task success rate: QA exact/normalized match (or judge), CODE = patch
        resolves / tests pass. Tracked memory-on vs memory-off so the
        dashboard can show the lift. Range [0, 1]; higher better.
    """

    recency: float = 0.0
    efficiency: float = 0.0
    relevancy: float = 0.0
    accuracy: float = 0.0
    # Auxiliary breakdowns (populated by compute_metrics; optional).
    recency_decayed: float = 0.0
    precision_at_k: float = 0.0
    accuracy_memory_off: Optional[float] = None
    n: int = 0  # number of tasks contributing to these metrics
    extra: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Flat JSON-serializable dict (for dashboards / aggregation)."""
        d = asdict(self)
        return d

    @property
    def accuracy_lift(self) -> Optional[float]:
        """memory-on accuracy minus memory-off accuracy, if both known."""
        if self.accuracy_memory_off is None:
            return None
        return self.accuracy - self.accuracy_memory_off


@dataclass(slots=True)
class RunResult:
    """Everything produced by one harness run over one benchmark + config.

    ``metrics`` is the aggregate over ``trajectories``. ``cost_usd`` and
    ``tokens_in``/``tokens_out`` come from the CostTracker. ``budget_exceeded``
    flags an early abort; ``partial`` is True when the run did not cover all
    tasks (limit, dev_slice, or budget abort). ``config`` and ``benchmark``
    identify the cell on the results grid.
    """

    benchmark: Benchmark
    config: ModelConfig
    metrics: Metrics
    trajectories: list[Trajectory] = field(default_factory=list)
    n_tasks: int = 0
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    budget_exceeded: bool = False
    partial: bool = False
    started_at: float = 0.0
    ended_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.ended_at - self.started_at)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable summary (omits full trajectories by default)."""
        return {
            "benchmark": self.benchmark.value,
            "config": asdict(self.config),
            "metrics": self.metrics.to_dict(),
            "n_tasks": self.n_tasks,
            "cost_usd": self.cost_usd,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "budget_exceeded": self.budget_exceeded,
            "partial": self.partial,
            "duration_s": self.duration_s,
            "metadata": self.metadata,
        }


__all__ = [
    "Benchmark",
    "TaskKind",
    "Session",
    "Task",
    "MemoryItem",
    "RetrievedItem",
    "TrajectoryStep",
    "Trajectory",
    "ModelConfig",
    "Metrics",
    "RunResult",
]
