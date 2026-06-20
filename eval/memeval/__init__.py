"""memeval -- evaluation infrastructure for the AI Agent Memory Harness.

Public surface is the frozen contract in :mod:`memeval.schema` and
:mod:`memeval.protocols`. Everything else (loaders, metrics, harness, cost,
trajectory, models) builds against those two. Importing this package pulls in
only the standard library; heavy deps are lazy-imported where used.
"""

from __future__ import annotations

__version__ = "0.1.0"

#: Version of the **memory system** (the memory code + storage as a whole), used
#: to bucket benchmark results under ``results/v{MEMORY_VERSION}/``. Bump this by
#: 0.1 whenever a change to the memory code/storage is paired with a new run, so
#: each generation's results live in their own versioned directory. This is
#: intentionally separate from ``__version__`` (the Python package version).
MEMORY_VERSION = "0.1"

from .schema import (
    Benchmark,
    Metrics,
    MemoryItem,
    ModelConfig,
    RetrievedItem,
    RunResult,
    Session,
    Task,
    TaskKind,
    Trajectory,
    TrajectoryStep,
)
from .protocols import Loader, MemoryStore, ModelAdapter

# Harness + helpers. The harness imports the sibling modules (loaders, metrics,
# cost, trajectory, models); all of those are stdlib-only on the offline path,
# so this stays import-light. Exposed at the package root so callers can do
# ``from memeval import run, InMemoryStore``.
from .harness import (
    InMemoryStore,
    cheapest_first,
    run,
    should_early_exit,
    stratified_dev_slice,
)

# Multi-step agent seam (for OpenCode and other agent-loop drivers). Sits beside
# the single-shot ``run`` and reuses the same RunResult/metrics/trajectory.
from .agent import (
    AgentAdapter,
    AgentContext,
    AgentResult,
    EchoAgent,
    function_agent,
    run_agent,
)

# Results ledger lives in ``memeval.results`` (append_result / load_results) — not
# re-exported here so ``python -m memeval.results`` runs without a double-import warning.

__all__ = [
    "__version__",
    "MEMORY_VERSION",
    # schema
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
    # protocols
    "MemoryStore",
    "ModelAdapter",
    "Loader",
    # harness
    "run",
    "InMemoryStore",
    "cheapest_first",
    "should_early_exit",
    "stratified_dev_slice",
    # agent seam
    "run_agent",
    "AgentAdapter",
    "AgentContext",
    "AgentResult",
    "EchoAgent",
    "function_agent",
]
