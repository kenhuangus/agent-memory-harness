"""LongMemEval loader.

Real source
-----------
* GitHub: ``xiaowu0162/LongMemEval``
* arXiv: 2410.10813
* Data files: ``longmemeval_s.json`` (~115k tok/q), ``longmemeval_m.json``
  (~1.5M tok/q), ``longmemeval_oracle.json``.

Each LongMemEval question carries multiple **timestamped** sessions (a user's
chat history). Five abilities are probed, including *temporal reasoning*,
*knowledge updates*, and *abstention* (questions that should be refused because
the answer is not in memory). The official schema fields:

* ``question_id``       -- unique id; the ``_abs`` suffix marks abstention items.
* ``question_type``     -- the ability (e.g. ``temporal-reasoning``,
                           ``knowledge-update``, ``single-session-user``).
* ``question`` / ``answer``
* ``question_date``     -- when the question is asked (recency reference time).
* ``haystack_sessions`` -- list of sessions, each a list of turn dicts.
* ``haystack_dates``    -- per-session timestamps (parallel to the sessions).
* ``haystack_session_ids`` -- per-session ids.
* ``answer_session_ids`` -- ids of the sessions containing the evidence
                            (-> ``gold_memory_ids``).

Offline parsing of a local ``longmemeval_*.json`` path / fixture is stdlib-only;
the remote path lazily downloads the JSON via ``requests`` (the dataset ships as
plain JSON files on GitHub, not a HF dataset).
"""

from __future__ import annotations

from typing import Any, Optional

from ..schema import Benchmark, Session, Task, TaskKind
from .base import BaseLoader, first_present, rows_of, to_epoch

#: Raw GitHub release files; the repo distributes plain JSON (not a HF dataset).
_VARIANT_URLS = {
    "longmemeval_s": (
        "https://raw.githubusercontent.com/xiaowu0162/LongMemEval/"
        "main/data/longmemeval_s.json"
    ),
    "longmemeval_m": (
        "https://raw.githubusercontent.com/xiaowu0162/LongMemEval/"
        "main/data/longmemeval_m.json"
    ),
    "longmemeval_oracle": (
        "https://raw.githubusercontent.com/xiaowu0162/LongMemEval/"
        "main/data/longmemeval_oracle.json"
    ),
}


class LongMemEvalLoader(BaseLoader):
    """Loader for LongMemEval (``xiaowu0162/LongMemEval``)."""

    benchmark: Benchmark = Benchmark.LONGMEMEVAL
    #: Default variant id; ``load(path_or_id="longmemeval_m")`` selects another.
    default_source: str = "longmemeval_s"
    kind: TaskKind = TaskKind.QA

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
        split: str = "test",
        **kwargs: Any,
    ) -> list[Task]:
        """Download a LongMemEval JSON variant (lazy ``requests`` import)."""
        url = _VARIANT_URLS.get(source, source)
        if not (url.startswith("http://") or url.startswith("https://")):
            url = _VARIANT_URLS.get("longmemeval_s")
        try:
            import requests  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dep
            raise RuntimeError(
                "Remote LongMemEval loading requires the optional 'requests' "
                "package. For offline use, download longmemeval_s.json and "
                f"pass its path. URL was {url!r}."
            ) from exc
        resp = requests.get(url, timeout=120)  # pragma: no cover - network
        resp.raise_for_status()  # pragma: no cover - network
        rows = rows_of(resp.json())  # pragma: no cover - network
        return self._parse_rows(rows, limit=limit)  # pragma: no cover

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
        qid = str(
            first_present(row, "question_id", "qid", "id", "question_id",
                          default=f"lme_{idx}")
        )
        question = str(first_present(row, "question", "query", default=""))
        answer = first_present(row, "answer", "gold", "expected_answer")
        answer = None if answer is None else str(answer)

        qtype = first_present(row, "question_type", "type", "ability",
                              "competency")
        competency = (
            str(qtype).strip().lower().replace("-", "_").replace(" ", "_")
            if qtype else None
        )

        # Question time = recency reference; stash it on metadata.
        q_date = first_present(row, "question_date", "date", "timestamp")
        metadata: dict[str, Any] = {}
        if q_date is not None:
            metadata["question_date"] = q_date
            metadata["created_at"] = to_epoch(q_date)
        # Abstention questions: id suffix ``_abs`` is the official marker.
        is_abstention = qid.endswith("_abs") or bool(
            first_present(row, "is_abstention", "abstention", default=False)
        )
        if is_abstention:
            metadata["abstention"] = True
            if competency is None:
                competency = "abstention"

        sessions = self._build_sessions(row)

        gold_raw = first_present(
            row, "answer_session_ids", "gold_memory_ids", "evidence_session_ids",
            "supporting_session_ids", default=[]
        )
        gold_memory_ids = [str(g) for g in _as_list(gold_raw)]

        return Task(
            task_id=qid,
            benchmark=self.benchmark,
            kind=TaskKind.QA,
            question=question,
            answer=answer,
            sessions=sessions,
            gold_memory_ids=gold_memory_ids,
            competency=competency,
            metadata=metadata,
        )

    def _build_sessions(self, row: dict) -> list[Session]:
        """Reconstruct sessions from the parallel haystack_* arrays.

        ``haystack_sessions`` is a list where each element is itself a list of
        turn dicts; ``haystack_dates`` and ``haystack_session_ids`` are parallel
        arrays giving each session's timestamp and id. Falls back to the generic
        normalizer when those arrays are absent.
        """
        haystack = first_present(row, "haystack_sessions", "sessions",
                                 "context")
        if not isinstance(haystack, (list, tuple)):
            from .base import sessions_from_any
            return sessions_from_any(haystack)

        dates = _as_list(first_present(row, "haystack_dates", default=[]))
        ids = _as_list(first_present(row, "haystack_session_ids", default=[]))

        sessions: list[Session] = []
        for i, sess in enumerate(haystack):
            sid = str(ids[i]) if i < len(ids) else f"session_{i}"
            ts = to_epoch(dates[i]) if i < len(dates) else 0.0
            content = _turns_to_text(sess)
            sessions.append(
                Session(
                    session_id=sid,
                    content=content,
                    timestamp=ts,
                    index=i,
                    role="user",
                    metadata={"date": dates[i]} if i < len(dates) else {},
                )
            )
        return sessions


def _turns_to_text(sess: Any) -> str:
    """Flatten a LongMemEval session (list of turn dicts) into text."""
    if isinstance(sess, str):
        return sess
    if isinstance(sess, dict):
        sess = sess.get("turns", sess.get("content", sess))
    if not isinstance(sess, (list, tuple)):
        return str(sess)
    parts: list[str] = []
    for turn in sess:
        if isinstance(turn, dict):
            role = turn.get("role", "")
            text = turn.get("content", turn.get("text", ""))
            parts.append(f"{role}: {text}" if role else str(text))
        else:
            parts.append(str(turn))
    return "\n".join(parts)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


__all__ = ["LongMemEvalLoader"]
