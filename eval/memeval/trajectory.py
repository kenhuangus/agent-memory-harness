"""Trajectory recording + JSONL persistence for the memory harness.

A :class:`~memeval.schema.Trajectory` is the per-task reproducibility log: the
ordered list of actions (retrieve / generate / write / judge / ...) one model
config took on one benchmark task, plus the final prediction and grade. This
module is the *only* place trajectories are serialized, so the on-disk schema
lives and is documented here.

There are two ways to produce a Trajectory, and both are supported:

1. **Builder API** (``TrajectoryLogger`` + ``start_task`` / ``step`` / ``end_task``)
   -- the ergonomic path the harness uses while a task is running. You open one
   task at a time, append steps as they happen, then close the task; the logger
   writes one JSONL line on ``end_task``/``close`` and returns the finished
   :class:`~memeval.schema.Trajectory`.

2. **Direct logging** (``TrajectoryLogger.log(traj)``) -- write an
   already-built :class:`~memeval.schema.Trajectory` straight to disk. This is
   the FROZEN-contract entry point that other workstreams call.

Reading is symmetric: :func:`read_trajectories` streams one Trajectory per
line (the dreaming worker, Scott B., consumes this), and
:func:`read_trajectory_list` materializes them.

On-disk format (the contract the dreaming worker reads)
-------------------------------------------------------
**JSON Lines**: one self-contained JSON object per line, one object per
Trajectory, UTF-8, ``\\n``-separated. Every schema dataclass round-trips
losslessly via :func:`trajectory_to_dict` / :func:`trajectory_from_dict`.
Enums (:class:`~memeval.schema.Benchmark`) serialize to their ``str`` value.
Nested ``RetrievedItem`` -> ``MemoryItem`` is preserved in full so a reader
needs nothing but this file.

Top-level keys of one line::

    {
      "task_id":    str,
      "benchmark":  str,          # Benchmark value, e.g. "longmemeval"
      "model":      str,          # model/config label, e.g. "haiku+mem"
      "memory_on":  bool,
      "prediction": str | null,
      "success":    bool | null,  # null == ungraded
      "started_at": float,        # explicit epoch seconds (never wall-clock)
      "ended_at":   float,
      "metadata":   object,
      "steps": [ <step>, ... ]
    }

Each ``<step>``::

    {
      "step":       int,          # 0-based position, renumbered on append
      "kind":       str,          # retrieve|generate|write|judge|error|note
      "content":    str,
      "timestamp":  float,
      "tokens_in":  int,
      "tokens_out": int,
      "retrieved":  [ <retrieved>, ... ],
      "metadata":   object        # may carry "written"/"used" provenance
    }

Each ``<retrieved>`` wraps a full MemoryItem plus its search-time score::

    {
      "score": float, "rank": int, "is_gold": bool,
      "item": {
        "item_id": str, "content": str, "timestamp": float,
        "relevancy": float, "session_id": str|null, "source": str|null,
        "tags": [str], "embedding": [float]|null, "tokens": int,
        "metadata": object
      }
    }

This module is standard-library only (``json``, ``pathlib``) and imports
cleanly on Python 3.11+.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator, Optional, Union

from .schema import (
    Benchmark,
    MemoryItem,
    RetrievedItem,
    Trajectory,
    TrajectoryStep,
)

__all__ = [
    "trajectory_to_dict",
    "trajectory_from_dict",
    "TrajectoryLogger",
    "read_trajectories",
    "read_trajectory_list",
]

# Step kinds the harness emits (mirrors TrajectoryStep docstring). Kept here so
# the builder can validate/normalize without re-importing the schema constant.
STEP_KINDS = ("retrieve", "generate", "write", "judge", "error", "note")


# --------------------------------------------------------------------------- #
# Serialization helpers (dataclass <-> plain dict, JSON-safe)
# --------------------------------------------------------------------------- #
def _memory_item_to_dict(item: MemoryItem) -> dict[str, Any]:
    """Serialize a :class:`MemoryItem` to a JSON-safe dict (all fields kept)."""
    return {
        "item_id": item.item_id,
        "content": item.content,
        "timestamp": item.timestamp,
        "relevancy": item.relevancy,
        "session_id": item.session_id,
        "source": item.source,
        "tags": list(item.tags),
        "embedding": list(item.embedding) if item.embedding is not None else None,
        "tokens": item.tokens,
        "version": item.version,
        "metadata": dict(item.metadata),
    }


def _memory_item_from_dict(d: dict[str, Any]) -> MemoryItem:
    """Rebuild a :class:`MemoryItem` from :func:`_memory_item_to_dict` output."""
    emb = d.get("embedding")
    return MemoryItem(
        item_id=d["item_id"],
        content=d.get("content", ""),
        timestamp=float(d.get("timestamp", 0.0)),
        relevancy=float(d.get("relevancy", 1.0)),
        session_id=d.get("session_id"),
        source=d.get("source"),
        tags=list(d.get("tags", [])),
        embedding=[float(x) for x in emb] if emb is not None else None,
        tokens=int(d.get("tokens", 0)),
        version=int(d.get("version", 1)),
        metadata=dict(d.get("metadata", {})),
    )


def _retrieved_to_dict(r: RetrievedItem) -> dict[str, Any]:
    """Serialize a :class:`RetrievedItem` (wraps a full MemoryItem)."""
    return {
        "score": r.score,
        "rank": r.rank,
        "is_gold": r.is_gold,
        "item": _memory_item_to_dict(r.item),
    }


def _retrieved_from_dict(d: dict[str, Any]) -> RetrievedItem:
    """Rebuild a :class:`RetrievedItem` from :func:`_retrieved_to_dict` output."""
    return RetrievedItem(
        item=_memory_item_from_dict(d["item"]),
        score=float(d.get("score", 0.0)),
        rank=int(d.get("rank", 0)),
        is_gold=bool(d.get("is_gold", False)),
    )


def _step_to_dict(s: TrajectoryStep) -> dict[str, Any]:
    """Serialize a :class:`TrajectoryStep` and its nested retrieved items."""
    return {
        "step": s.step,
        "kind": s.kind,
        "content": s.content,
        "timestamp": s.timestamp,
        "tokens_in": s.tokens_in,
        "tokens_out": s.tokens_out,
        "retrieved": [_retrieved_to_dict(r) for r in s.retrieved],
        "metadata": dict(s.metadata),
    }


def _step_from_dict(d: dict[str, Any]) -> TrajectoryStep:
    """Rebuild a :class:`TrajectoryStep` from :func:`_step_to_dict` output."""
    return TrajectoryStep(
        step=int(d.get("step", 0)),
        kind=d.get("kind", "note"),
        content=d.get("content", ""),
        timestamp=float(d.get("timestamp", 0.0)),
        retrieved=[_retrieved_from_dict(r) for r in d.get("retrieved", [])],
        tokens_in=int(d.get("tokens_in", 0)),
        tokens_out=int(d.get("tokens_out", 0)),
        metadata=dict(d.get("metadata", {})),
    )


def trajectory_to_dict(traj: Trajectory) -> dict[str, Any]:
    """Convert a :class:`Trajectory` into a JSON-serializable dict.

    Lossless: every field of the Trajectory and all nested dataclasses
    (steps -> retrieved -> item) survive a ``to_dict``/``from_dict`` round
    trip. The :class:`Benchmark` enum is written as its ``str`` value.
    """
    return {
        "task_id": traj.task_id,
        "benchmark": traj.benchmark.value
        if isinstance(traj.benchmark, Benchmark)
        else str(traj.benchmark),
        "model": traj.model,
        "memory_on": traj.memory_on,
        "prediction": traj.prediction,
        "success": traj.success,
        "started_at": traj.started_at,
        "ended_at": traj.ended_at,
        "metadata": dict(traj.metadata),
        "steps": [_step_to_dict(s) for s in traj.steps],
    }


def trajectory_from_dict(d: dict[str, Any]) -> Trajectory:
    """Rebuild a :class:`Trajectory` (and all nested dataclasses) from a dict.

    Inverse of :func:`trajectory_to_dict`. The ``benchmark`` field is parsed
    leniently via :meth:`Benchmark.from_str` so hand-written fixtures with
    loose spelling still load.
    """
    bench_raw = d["benchmark"]
    benchmark = (
        bench_raw
        if isinstance(bench_raw, Benchmark)
        else Benchmark.from_str(str(bench_raw))
    )
    return Trajectory(
        task_id=d["task_id"],
        benchmark=benchmark,
        model=d.get("model", ""),
        memory_on=bool(d.get("memory_on", False)),
        steps=[_step_from_dict(s) for s in d.get("steps", [])],
        prediction=d.get("prediction"),
        success=d.get("success"),
        started_at=float(d.get("started_at", 0.0)),
        ended_at=float(d.get("ended_at", 0.0)),
        metadata=dict(d.get("metadata", {})),
    )


# --------------------------------------------------------------------------- #
# Writer
# --------------------------------------------------------------------------- #
class TrajectoryLogger:
    """Append-mode JSONL writer + per-task trajectory builder.

    Two complementary ways to use it, share one open file handle:

    **Direct** -- you already have a finished Trajectory::

        with TrajectoryLogger("runs/lme.jsonl") as log:
            log.log(trajectory)

    **Builder** -- you assemble a task's steps as the harness runs it::

        log = TrajectoryLogger("runs/lme.jsonl")
        log.start_task("q1", model="haiku+mem", memory_on=True,
                       benchmark="longmemeval", started_at=t0)
        log.step(retrieved=hits, tokens_in=120, tokens_out=18,
                 kind="generate", content=completion, timestamp=t1)
        traj = log.end_task(prediction=completion, success=True, ended_at=t2)
        log.close()

    Only one task may be open at a time; :meth:`start_task` raises if a task is
    already in progress. ``end_task`` writes the line and returns the finished
    :class:`Trajectory`. The file is flushed after every write so a crashed run
    still yields readable partial output.

    Determinism: no wall-clock is read here -- all timestamps are explicit
    arguments. ``started_at``/``ended_at`` default to ``0.0`` when omitted.
    """

    def __init__(self, path: Union[str, Path], *, append: bool = True) -> None:
        """Open ``path`` for writing.

        ``append=True`` (default) preserves existing lines; ``append=False``
        truncates. Parent directories are created if missing.
        """
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        self._fh = self.path.open(mode, encoding="utf-8")
        self._open: Optional[Trajectory] = None  # task currently being built

    # -- direct path ------------------------------------------------------- #
    def log(self, traj: Trajectory) -> None:
        """Write one finished :class:`Trajectory` as a single JSONL line.

        ``ensure_ascii=False`` keeps non-ASCII content human-readable on disk.
        Flushed immediately so partial runs are recoverable.
        """
        line = json.dumps(trajectory_to_dict(traj), ensure_ascii=False)
        self._fh.write(line + "\n")
        self._fh.flush()

    # -- builder path ------------------------------------------------------ #
    def start_task(
        self,
        task_id: str,
        *,
        model: str,
        memory_on: bool = False,
        benchmark: Union[Benchmark, str] = Benchmark.LONGMEMEVAL,
        started_at: float = 0.0,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Trajectory:
        """Begin building a Trajectory for ``task_id`` and return it.

        Raises ``RuntimeError`` if a task is already open (call
        :meth:`end_task` first). The returned object is the same one steps are
        appended to, so callers may inspect it mid-flight.
        """
        if self._open is not None:
            raise RuntimeError(
                f"task {self._open.task_id!r} is still open; call end_task() first"
            )
        bench = (
            benchmark
            if isinstance(benchmark, Benchmark)
            else Benchmark.from_str(str(benchmark))
        )
        self._open = Trajectory(
            task_id=task_id,
            benchmark=bench,
            model=model,
            memory_on=memory_on,
            started_at=started_at,
            metadata=dict(metadata or {}),
        )
        return self._open

    def step(
        self,
        *,
        written: Optional[list[MemoryItem]] = None,
        retrieved: Optional[list[RetrievedItem]] = None,
        used: Optional[list[RetrievedItem]] = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        kind: str = "generate",
        content: str = "",
        timestamp: float = 0.0,
        metadata: Optional[dict[str, Any]] = None,
    ) -> TrajectoryStep:
        """Append one action to the open task and return the recorded step.

        Parameters
        ----------
        written
            Memory items written *during* this step (the write path). Stored in
            ``step.metadata["written"]`` as a list of item ids so the efficiency
            metric (which keys off ``retrieved``) is not polluted, while the
            dreaming worker can still see what was learned.
        retrieved
            All items the search returned. Becomes ``step.retrieved`` -- the
            canonical field the recency/efficiency/relevancy metrics read.
            ``RetrievedItem.tokens`` (via the wrapped item) drives efficiency,
            and ``rank`` drives recency, so pass them already ranked/tokened.
        used
            The subset of ``retrieved`` actually fed to the model (after any
            re-rank/trim). Stored as ``step.metadata["used"]`` = list of item
            ids. ``None`` means "all of retrieved was used".
        tokens_in, tokens_out
            Prompt / completion token counts for a generate step (the
            ``total_tokens`` denominator of the efficiency metric).

        Raises ``RuntimeError`` if no task is open.
        """
        if self._open is None:
            raise RuntimeError("no open task; call start_task() before step()")

        retrieved = list(retrieved or [])
        meta: dict[str, Any] = dict(metadata or {})
        if written:
            meta["written"] = [m.item_id for m in written]
        if used is not None:
            meta["used"] = [r.item_id for r in used]

        s = TrajectoryStep(
            step=0,  # renumbered by Trajectory.add
            kind=kind,
            content=content,
            timestamp=timestamp,
            retrieved=retrieved,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            metadata=meta,
        )
        return self._open.add(s)

    def end_task(
        self,
        *,
        prediction: Optional[str] = None,
        success: Optional[bool] = None,
        ended_at: float = 0.0,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Trajectory:
        """Finalize the open task, write its JSONL line, and return it.

        ``success=None`` records an ungraded task. Extra ``metadata`` is merged
        into the trajectory's metadata. After this returns the logger is ready
        for the next :meth:`start_task`. Raises ``RuntimeError`` if no task is
        open.
        """
        if self._open is None:
            raise RuntimeError("no open task; call start_task() before end_task()")
        traj = self._open
        if prediction is not None:
            traj.prediction = prediction
        if success is not None:
            traj.success = success
        traj.ended_at = ended_at
        if metadata:
            traj.metadata.update(metadata)
        self.log(traj)
        self._open = None
        return traj

    # -- lifecycle --------------------------------------------------------- #
    def close(self) -> None:
        """Flush and close the file. If a task is open, write it first.

        Closing with an unfinished task auto-finalizes it (so no in-progress
        work is silently dropped) before releasing the handle. Idempotent.
        """
        if self._open is not None:
            # Auto-finalize the dangling task so its work is not lost.
            self.log(self._open)
            self._open = None
        if not self._fh.closed:
            self._fh.flush()
            self._fh.close()

    def __enter__(self) -> "TrajectoryLogger":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# Reader (consumed by the dreaming worker)
# --------------------------------------------------------------------------- #
def read_trajectories(path: Union[str, Path]) -> Iterator[Trajectory]:
    """Stream :class:`Trajectory` objects from a JSONL file, one per line.

    Blank lines are skipped (tolerates trailing newlines / hand edits). This
    is the streaming entry point the dreaming worker uses to replay runs
    without loading the whole file into memory. Raises ``FileNotFoundError``
    if ``path`` does not exist.
    """
    p = Path(path)
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            yield trajectory_from_dict(json.loads(line))


def read_trajectory_list(path: Union[str, Path]) -> list[Trajectory]:
    """Materialize all trajectories from a JSONL file into a list.

    Convenience wrapper over :func:`read_trajectories` for callers (e.g. the
    metrics layer) that want random access rather than a one-pass stream.
    """
    return list(read_trajectories(path))
