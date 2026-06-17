"""SWE-ContextBench loader.

Real source
-----------
* arXiv: 2602.08316 (HF paper page huggingface.co/papers/2602.08316)
* 1,100 base + 376 related tasks, 51 repos, 9 languages.
* Scored on accuracy / time / cost. Tasks are grouped by **shared context**
  (``group_id``): related tasks reuse the same repository context, so a memory
  harness should be able to amortize understanding across a group.

Normalization
-------------
Each task becomes one :class:`Task` (``TaskKind.CODE``):

* ``question``    <- the problem / issue statement.
* ``repo`` / ``base_commit`` / ``patch`` / ``test_patch`` <- code fields.
* ``fail_to_pass`` / ``pass_to_pass`` <- the test selectors graded for success.
* ``group_id``    <- the shared-context group id.
* ``order``       <- within-group position (so memory accrues over a group).
* ``competency``  <- the task language (used as a stratification key).
* ``sessions``    <- prior tasks' context within the same group, when the
                     dataset bundles a context blob (gives the memory store
                     something to retrieve).

Offline parsing of a local JSON path / fixture is stdlib-only; the HF download
path lazily imports ``datasets`` via the base loader.
"""

from __future__ import annotations

from typing import Any, Optional

from ..schema import Benchmark, Session, Task, TaskKind
from .base import BaseLoader, first_present, sessions_from_any, to_epoch


class SWEContextBenchLoader(BaseLoader):
    """Loader for SWE-ContextBench (arXiv 2602.08316)."""

    benchmark: Benchmark = Benchmark.SWE_CONTEXTBENCH
    default_source: str = "swe-contextbench/SWE-ContextBench"
    kind: TaskKind = TaskKind.CODE

    def _load_local(
        self, path: str, *, limit: Optional[int] = None, **kwargs: Any
    ) -> list[Task]:
        rows = self._read_rows(path)
        return self._parse_rows(rows, limit=limit)

    def _parse_rows(
        self, rows: list[dict], *, limit: Optional[int] = None
    ) -> list[Task]:
        # Assign within-group ``order`` by stable first-seen sequence per group.
        group_counters: dict[str, int] = {}
        tasks: list[Task] = []
        for i, row in enumerate(rows):
            task = self._row_to_task(row, i, group_counters)
            tasks.append(task)
            if limit is not None and len(tasks) >= limit:
                break
        return tasks

    def _row_to_task(
        self, row: dict, idx: int, group_counters: dict[str, int]
    ) -> Task:
        task_id = str(
            first_present(row, "task_id", "instance_id", "id",
                          default=f"scb_{idx}")
        )
        question = str(
            first_present(row, "problem_statement", "question", "issue",
                          "prompt", "instruction", default="")
        )
        repo = first_present(row, "repo", "repository", "repo_name")
        repo = None if repo is None else str(repo)
        base_commit = first_present(row, "base_commit", "commit", "sha")
        base_commit = None if base_commit is None else str(base_commit)
        patch = first_present(row, "patch", "gold_patch", "solution")
        patch = None if patch is None else str(patch)
        test_patch = first_present(row, "test_patch", "tests_patch")
        test_patch = None if test_patch is None else str(test_patch)

        fail_to_pass = _parse_test_list(
            first_present(row, "fail_to_pass", "FAIL_TO_PASS", default=[])
        )
        pass_to_pass = _parse_test_list(
            first_present(row, "pass_to_pass", "PASS_TO_PASS", default=[])
        )

        group_id = first_present(row, "group_id", "context_group", "group",
                                 "context_id")
        group_id = None if group_id is None else str(group_id)

        # Within-group ordering: explicit if present, else first-seen counter.
        if "order" in row and row["order"] is not None:
            order = int(row["order"])
        elif group_id is not None:
            order = group_counters.get(group_id, 0)
            group_counters[group_id] = order + 1
        else:
            order = 0

        # Language as the stratification competency.
        competency = first_present(row, "language", "lang", "competency")
        competency = None if competency is None else str(competency).lower()

        sessions = self._context_sessions(row, group_id, order)

        gold_raw = first_present(row, "gold_memory_ids", "context_ids",
                                 default=[])
        gold_memory_ids = [str(g) for g in _as_list(gold_raw)]

        metadata: dict[str, Any] = {}
        for key in ("language", "n_files", "difficulty", "related"):
            if key in row:
                metadata[key] = row[key]

        return Task(
            task_id=task_id,
            benchmark=self.benchmark,
            kind=TaskKind.CODE,
            question=question,
            answer=None,
            sessions=sessions,
            gold_memory_ids=gold_memory_ids,
            group_id=group_id,
            order=order,
            repo=repo,
            base_commit=base_commit,
            patch=patch,
            test_patch=test_patch,
            fail_to_pass=fail_to_pass,
            pass_to_pass=pass_to_pass,
            competency=competency,
            metadata=metadata,
        )

    def _context_sessions(
        self, row: dict, group_id: Optional[str], order: int
    ) -> list[Session]:
        """Build retrievable sessions from any bundled shared-context blob.

        SWE-ContextBench groups share repo context; when the row carries a
        ``context``/``hints``/``readme`` blob we expose it as a session so the
        memory store has something to retrieve and amortize across the group.
        """
        raw_ctx = first_present(row, "sessions", "context", "context_sessions")
        if raw_ctx is not None:
            return sessions_from_any(raw_ctx)
        blob = first_present(row, "hints_text", "readme", "context_text")
        if not blob:
            return []
        sid = f"{group_id or 'ctx'}_{order}"
        return [
            Session(
                session_id=sid,
                content=str(blob),
                timestamp=to_epoch(first_present(row, "timestamp", "date")),
                index=order,
                role="system",
                metadata={"group_id": group_id} if group_id else {},
            )
        ]


def _parse_test_list(value: Any) -> list[str]:
    """Normalize a FAIL_TO_PASS / PASS_TO_PASS field into a list of strings.

    SWE-bench-style datasets store these as a list OR a JSON-encoded string.
    """
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                import json

                parsed = json.loads(s)
                return [str(x) for x in parsed]
            except Exception:
                pass
        return [s] if s else []
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value]
    return [str(value)]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


__all__ = ["SWEContextBenchLoader"]
