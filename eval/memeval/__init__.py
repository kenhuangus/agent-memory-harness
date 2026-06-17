"""memeval -- evaluation infrastructure for the AI Agent Memory Harness.

Public surface is the frozen contract in :mod:`memeval.schema` and
:mod:`memeval.protocols`. Everything else (loaders, metrics, harness, cost,
trajectory, models) builds against those two. Importing this package pulls in
only the standard library; heavy deps are lazy-imported where used.
"""

from __future__ import annotations

__version__ = "0.1.0"

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

__all__ = [
    "__version__",
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
]
