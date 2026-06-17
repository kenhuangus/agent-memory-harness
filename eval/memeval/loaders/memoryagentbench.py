"""MemoryAgentBench loader.

Real source
-----------
* HuggingFace dataset: ``ai-hyz/MemoryAgentBench``
* GitHub: ``HUST-AI-HYZ/MemoryAgentBench``
* arXiv: 2507.05257

MemoryAgentBench probes four memory competencies -- *accurate retrieval*,
*test-time learning*, *long-range understanding*, and *conflict resolution* --
across subsets including ``EventQA`` and ``FactConsolidation``. Each example is
a long interaction history (many chunks/sessions) plus one or more questions
whose answers depend on remembering, updating, or reconciling facts from that
history.

Normalization
-------------
Each example becomes one :class:`Task` (``TaskKind.QA``):

* ``sessions``        <- the history chunks (``context``/``chunks``/``history``),
                          timestamped for the recency metric.
* ``question``/``answer`` <- the example's query + gold answer.
* ``competency``      <- the competency / subset label (stratification key).
* ``gold_memory_ids`` <- ids of the chunks marked as evidence, when present.

Offline parsing of a local JSON path / fixture is stdlib-only; the HF download
path lazily imports ``datasets`` via the base loader.
"""

from __future__ import annotations

from typing import Any, Optional

from ..schema import Benchmark, Task, TaskKind
from .base import (
    BaseLoader,
    first_present,
    sessions_from_any,
    to_epoch,
)

#: Map raw subset/ability names onto the four canonical competencies.
_COMPETENCY_ALIASES = {
    "eventqa": "accurate_retrieval",
    "accurate_retrieval": "accurate_retrieval",
    "retrieval": "accurate_retrieval",
    "factconsolidation": "conflict_resolution",
    "fact_consolidation": "conflict_resolution",
    "conflict_resolution": "conflict_resolution",
    "conflict": "conflict_resolution",
    "test_time_learning": "test_time_learning",
    "ttl": "test_time_learning",
    "long_range_understanding": "long_range_understanding",
    "long_range": "long_range_understanding",
    "lru": "long_range_understanding",
}


def _canon_competency(raw: Any) -> Optional[str]:
    """Canonicalize a subset/ability label to one of the four competencies."""
    if not raw:
        return None
    key = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    return _COMPETENCY_ALIASES.get(key, key)


class MemoryAgentBenchLoader(BaseLoader):
    """Loader for MemoryAgentBench (``ai-hyz/MemoryAgentBench``)."""

    benchmark: Benchmark = Benchmark.MEMORY_AGENT_BENCH
    default_source: str = "ai-hyz/MemoryAgentBench"
    kind: TaskKind = TaskKind.QA

    #: The dataset ships one split per competency (no generic ``test`` split).
    _SPLITS = (
        "Accurate_Retrieval",
        "Test_Time_Learning",
        "Long_Range_Understanding",
        "Conflict_Resolution",
    )

    def _load_local(
        self, path: str, *, limit: Optional[int] = None, **kwargs: Any
    ) -> list[Task]:
        rows = self._read_rows(path)
        return self._parse_rows(rows, limit=limit)

    def _load_remote(
        self,
        source: str,
        *,
        limit: Optional[int] = None,
        split: Optional[str] = None,
        **kwargs: Any,
    ) -> list[Task]:
        """Load MemoryAgentBench from its per-competency HF splits.

        The dataset has no ``test`` split; it ships four competency splits, each
        a set of rows shaped ``{context, questions[], answers[], metadata}`` --
        one long context shared by *many* questions. We expand every
        ``(context, question[i], answer[i])`` into one flat row (competency =
        split name), then reuse the shared row parser. ``split`` restricts to a
        single competency; otherwise all four are read in order. ``datasets`` is
        imported lazily by :meth:`_expand_split`.
        """
        # load() passes split="test" by default; only honor an explicit, known
        # competency split, otherwise read all four.
        splits = [split] if split in self._SPLITS else list(self._SPLITS)
        rows: list[dict] = []
        for sp in splits:
            for row in self._expand_split(source, sp):
                rows.append(row)
                if limit is not None and len(rows) >= limit:
                    return self._parse_rows(rows, limit=limit)
        return self._parse_rows(rows, limit=limit)

    def _expand_split(self, source: str, split: str) -> "list[dict]":
        """Stream one competency split, expanding parallel Q/A into flat rows."""
        try:
            from datasets import load_dataset  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dep
            raise RuntimeError(
                "MemoryAgentBench remote loading needs the optional 'datasets' "
                "package (`pip install datasets`). For offline use, pass a local "
                "JSON path instead."
            ) from exc
        competency = _canon_competency(split) or split.lower()
        ds = load_dataset(source, split=split, streaming=True)  # pragma: no cover
        out: list[dict] = []
        for ridx, row in enumerate(ds):  # pragma: no cover - network
            context = row.get("context")
            questions = row.get("questions") or []
            answers = row.get("answers") or []
            meta = row.get("metadata") or {}
            for qi, q in enumerate(questions):
                ans = answers[qi] if qi < len(answers) else None
                # answers[i] is itself a list of acceptable answers; take the
                # first as gold and keep the rest in metadata.
                if isinstance(ans, (list, tuple)):
                    gold = str(ans[0]) if ans else None
                    alts = [str(a) for a in ans]
                else:
                    gold = None if ans is None else str(ans)
                    alts = [] if ans is None else [str(ans)]
                out.append({
                    "task_id": f"mab_{split}_{ridx}_{qi}",
                    "question": q,
                    "answer": gold,
                    "context": context,
                    "competency": competency,
                    "subset": split,
                    "metadata": meta,
                    "acceptable_answers": alts,
                })
        return out

    def _parse_rows(
        self, rows: list[dict], *, limit: Optional[int] = None
    ) -> list[Task]:
        tasks: list[Task] = []
        for i, row in enumerate(rows):
            tasks.append(self._row_to_task(row, i))
            if limit is not None and len(tasks) >= limit:
                break
        return tasks

    def _row_to_task(self, row: dict, idx: int) -> Task:
        task_id = str(
            first_present(row, "task_id", "id", "qid", "example_id",
                          default=f"mab_{idx}")
        )
        question = str(
            first_present(row, "question", "query", "input", "prompt",
                          default="")
        )
        answer = first_present(row, "answer", "gold", "label", "output",
                               "target")
        answer = None if answer is None else str(answer)

        # History/context can live under several keys.
        raw_sessions = first_present(
            row, "sessions", "context", "chunks", "history",
            "haystack_sessions", "documents",
        )
        sessions = sessions_from_any(raw_sessions)

        competency = _canon_competency(
            first_present(row, "competency", "ability", "subset", "category",
                          "task_type")
        )

        gold_ids = first_present(
            row, "gold_memory_ids", "evidence", "evidence_ids",
            "answer_session_ids", "gold_chunks", default=[]
        )
        gold_memory_ids = [str(g) for g in _as_list(gold_ids)]

        # Surface the example timestamp into metadata; sessions already carry
        # their own per-session timestamps used by the recency metric.
        metadata: dict[str, Any] = {}
        if "subset" in row:
            metadata["subset"] = row["subset"]
        ts = first_present(row, "timestamp", "time", "date")
        if ts is not None:
            metadata["created_at"] = to_epoch(ts)

        choices = first_present(row, "choices", "options")
        choices = list(choices) if isinstance(choices, (list, tuple)) else None

        return Task(
            task_id=task_id,
            benchmark=self.benchmark,
            kind=TaskKind.QA,
            question=question,
            answer=answer,
            choices=choices,
            sessions=sessions,
            gold_memory_ids=gold_memory_ids,
            competency=competency,
            metadata=metadata,
        )


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


__all__ = ["MemoryAgentBenchLoader"]
