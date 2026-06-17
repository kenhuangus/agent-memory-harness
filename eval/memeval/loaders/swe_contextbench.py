"""SWE-ContextBench loader.

Real source
-----------
* HuggingFace dataset: ``jiayuanz3/SWEContextBench`` (validated).
* GitHub: github.com/jiayuanz3/SWEContextBench · arXiv: 2602.08316.
* 1,100 base + 376 related tasks, 51 repos, 9 languages; built on SWE-bench
  Lite / Multilingual / Verified, so rows use the SWE-bench column schema
  (``instance_id, patch, repo, base_commit, hints_text, created_at, test_patch,
  problem_statement, version, environment_setup_commit, FAIL_TO_PASS, PASS_TO_PASS``).
* Ships parquet files (NOT a single ``test`` split): ``SWEContextBench_Experience``
  (1,100 base), ``SWEContextBench_Related`` (376), ``SWEContextBench_Lite_*``
  (smaller subsets; pass ``lite=True``), and ``SWEContextBench_Relationship``
  (related->experience links). ``group_id`` is taken from those links (the
  shared-context group) so a memory harness can amortize understanding across it.
* Real CODE scoring (apply patch, run FAIL_TO_PASS/PASS_TO_PASS) uses the repo's
  own ``evaluation.sh <run_id> {lite|full}`` harness; this loader prepares tasks
  and records predictions/trajectories.

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
    default_source: str = "jiayuanz3/SWEContextBench"
    kind: TaskKind = TaskKind.CODE

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
        lite: bool = False,
        **kwargs: Any,
    ) -> list[Task]:
        """Load SWE-ContextBench from its HuggingFace parquet files.

        Unlike a normal HF dataset this repo has no single split; it ships
        separate parquet files. We load Experience first (so the base task is
        order 0 within its group), then Related, and consult the Relationship
        file to set ``group_id`` = the experience instance each related task
        reuses. ``datasets`` is imported lazily so the offline path stays
        stdlib-only. Pass ``lite=True`` for the smaller Lite_* subsets.
        """
        try:
            from datasets import load_dataset  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dep
            raise RuntimeError(
                "SWE-ContextBench remote loading needs the optional 'datasets' "
                "package (`pip install datasets`). For offline use, pass a local "
                "JSON path instead."
            ) from exc

        prefix = "SWEContextBench_Lite_" if lite else "SWEContextBench_"
        exp_file = f"{prefix}Experience.parquet"
        rel_file = f"{prefix}Related.parquet"

        # related_instance_id -> experience_instance_id (shared-context group root)
        rel_map: dict[str, str] = {}
        try:  # pragma: no cover - network
            rel_ds = load_dataset(
                source,
                data_files="SWEContextBench_Relationship.parquet",
                split="train",
            )
            for r in rel_ds:
                rid = r.get("related_instance_id")
                eid = r.get("experience_instance_id")
                if rid is not None and eid is not None:
                    rel_map[str(rid)] = str(eid)
        except Exception:  # pragma: no cover - relationship file optional
            rel_map = {}

        rows: list[dict] = []
        for fname in (exp_file, rel_file):
            try:  # pragma: no cover - network
                ds = load_dataset(source, data_files=fname, split="train")
            except Exception:
                continue
            for row in ds:  # pragma: no cover - network
                d = dict(row)
                iid = str(d.get("instance_id", ""))
                d.setdefault(
                    "group_id", rel_map.get(iid) or d.get("repo") or iid
                )
                rows.append(d)
                if limit is not None and len(rows) >= limit:
                    break
            if limit is not None and len(rows) >= limit:
                break
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
                timestamp=to_epoch(
                    first_present(row, "timestamp", "date", "created_at")
                ),
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
