"""SWE-Bench-CL loader.

Real source
-----------
* GitHub: ``thomasjoshi/agents-never-forget``
* arXiv: 2507.00014
* Built on SWE-bench Verified, reorganized into chronologically ordered
  **sequences** of issues per repository for **continual learning** evaluation.

The dataset is structured as a list of *sequences*; each sequence is one repo's
issues in chronological order. Continual-learning metrics (e.g. forgetting,
forward transfer) need that order preserved -- so each task records:

* ``group_id`` <- the sequence id (the per-repo continual-learning sequence).
* ``order``    <- the within-sequence chronological position.

Normalization
-------------
Each issue (a SWE-bench instance) becomes one :class:`Task` (``TaskKind.CODE``):

* ``question``  <- the issue / problem statement.
* ``repo`` / ``base_commit`` / ``patch`` / ``test_patch``.
* ``fail_to_pass`` / ``pass_to_pass`` <- graded test selectors.
* ``sessions``  <- earlier issues in the same sequence (their problem +
                   solution) become retrievable memories, modeling what a
                   continually-learning agent would have accumulated.
* ``gold_memory_ids`` <- ids of the prior sequence issues (the memories an
                          ideal agent would carry forward).

Offline parsing of a local JSON path / fixture is stdlib-only; the remote path
lazily imports ``datasets`` (HF mirror) or falls back to a GitHub JSON download
via ``requests``.
"""

from __future__ import annotations

from typing import Any, Optional

from ..schema import Benchmark, Session, Task, TaskKind
from .base import BaseLoader, first_present, rows_of, to_epoch


class SWEBenchCLLoader(BaseLoader):
    """Loader for SWE-Bench-CL (``thomasjoshi/agents-never-forget``)."""

    benchmark: Benchmark = Benchmark.SWE_BENCH_CL
    default_source: str = "thomasjoshi/agents-never-forget"
    kind: TaskKind = TaskKind.CODE

    def _load_local(
        self, path: str, *, limit: Optional[int] = None, **kwargs: Any
    ) -> list[Task]:
        from .base import read_json

        data = read_json(path)
        rows = self._flatten_sequences(data)
        return self._parse_rows(rows, limit=limit)

    def _load_remote(
        self,
        source: str,
        *,
        limit: Optional[int] = None,
        split: str = "test",
        **kwargs: Any,
    ) -> list[Task]:
        """Fetch the SWE-Bench-CL dataset (lazy ``requests`` import).

        The repo ships ``SWE-Bench-CL.json`` (a list of sequences). We download
        and flatten it; ``datasets`` is not required for this source.
        """
        url = source
        if not (url.startswith("http://") or url.startswith("https://")):
            url = (
                "https://raw.githubusercontent.com/thomasjoshi/"
                "agents-never-forget/main/SWE-Bench-CL.json"
            )
        try:
            import requests  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dep
            raise RuntimeError(
                "Remote SWE-Bench-CL loading requires the optional 'requests' "
                "package. For offline use, download SWE-Bench-CL.json and pass "
                f"its path. URL was {url!r}."
            ) from exc
        resp = requests.get(url, timeout=120)  # pragma: no cover - network
        resp.raise_for_status()  # pragma: no cover - network
        rows = self._flatten_sequences(resp.json())  # pragma: no cover
        return self._parse_rows(rows, limit=limit)  # pragma: no cover

    # -- sequence flattening ------------------------------------------------ #
    def _flatten_sequences(self, data: Any) -> list[dict]:
        """Flatten the nested ``sequences -> tasks`` shape into flat rows.

        Accepts either the native shape ``{"sequences": [{"id"|"repo": ...,
        "tasks": [...]}]}`` (or a bare list of such sequences) OR an already-flat
        list of instances (each carrying its own ``sequence``/``group_id``).
        Annotates each flattened row with ``group_id`` and ``order``.
        """
        sequences: list[dict] = []
        if isinstance(data, dict) and "sequences" in data:
            seqs = data.get("sequences") or []
        elif isinstance(data, list) and data and all(
            isinstance(s, dict) and ("tasks" in s or "instances" in s)
            for s in data
        ):
            seqs = data
        else:
            # Already flat -> ensure each row has group_id/order, then return.
            flat = rows_of(data)
            return self._annotate_flat(flat)

        rows: list[dict] = []
        for s in seqs:
            seq_id = str(
                first_present(s, "id", "sequence_id", "repo", "name",
                              default=f"seq_{len(sequences)}")
            )
            sequences.append(s)
            tasks = first_present(s, "tasks", "instances", default=[]) or []
            for order, t in enumerate(tasks):
                if not isinstance(t, dict):
                    continue
                row = dict(t)
                row.setdefault("group_id", seq_id)
                row.setdefault("order", order)
                rows.append(row)
        return rows

    def _annotate_flat(self, flat: list[dict]) -> list[dict]:
        """Ensure flat rows have group_id (from ``sequence``) + per-group order."""
        counters: dict[str, int] = {}
        out: list[dict] = []
        for r in flat:
            row = dict(r)
            gid = first_present(row, "group_id", "sequence", "sequence_id",
                                "repo")
            gid = str(gid) if gid is not None else "default"
            row["group_id"] = gid
            if "order" not in row or row["order"] is None:
                row["order"] = counters.get(gid, 0)
                counters[gid] = row["order"] + 1
            out.append(row)
        return out

    # -- row parsing -------------------------------------------------------- #
    def _parse_rows(
        self, rows: list[dict], *, limit: Optional[int] = None
    ) -> list[Task]:
        # Group rows so prior-issue context can be wired as memories.
        by_group: dict[str, list[dict]] = {}
        for r in rows:
            gid = str(r.get("group_id", "default"))
            by_group.setdefault(gid, []).append(r)
        for gid in by_group:
            by_group[gid].sort(key=lambda r: int(r.get("order", 0)))

        tasks: list[Task] = []
        for r in rows:
            gid = str(r.get("group_id", "default"))
            order = int(r.get("order", 0))
            priors = [
                p for p in by_group[gid] if int(p.get("order", 0)) < order
            ]
            tasks.append(self._row_to_task(r, len(tasks), priors))
            if limit is not None and len(tasks) >= limit:
                break
        return tasks

    def _row_to_task(
        self, row: dict, idx: int, priors: list[dict]
    ) -> Task:
        task_id = str(
            first_present(row, "task_id", "instance_id", "id",
                          default=f"swecl_{idx}")
        )
        question = str(
            first_present(row, "problem_statement", "question", "issue",
                          "text", default="")
        )
        repo = first_present(row, "repo", "repository")
        repo = None if repo is None else str(repo)
        base_commit = first_present(row, "base_commit", "commit")
        base_commit = None if base_commit is None else str(base_commit)
        patch = first_present(row, "patch", "gold_patch", "solution_patch")
        patch = None if patch is None else str(patch)
        test_patch = first_present(row, "test_patch", "tests_patch")
        test_patch = None if test_patch is None else str(test_patch)

        fail_to_pass = _parse_test_list(
            first_present(row, "fail_to_pass", "FAIL_TO_PASS", default=[])
        )
        pass_to_pass = _parse_test_list(
            first_present(row, "pass_to_pass", "PASS_TO_PASS", default=[])
        )

        group_id = str(row.get("group_id", "default"))
        order = int(row.get("order", 0))

        # Prior issues in the sequence become retrievable memories; an ideal
        # continual-learning agent carries them forward (-> gold_memory_ids).
        sessions: list[Session] = []
        gold_memory_ids: list[str] = []
        for p in priors:
            pid = str(
                first_present(p, "task_id", "instance_id", "id",
                              default=f"{group_id}_{p.get('order', 0)}")
            )
            p_problem = str(
                first_present(p, "problem_statement", "question", default="")
            )
            p_patch = str(first_present(p, "patch", "gold_patch", default=""))
            content = p_problem
            if p_patch:
                content = f"{p_problem}\n\n[solution]\n{p_patch}"
            sessions.append(
                Session(
                    session_id=pid,
                    content=content,
                    timestamp=to_epoch(
                        first_present(p, "created_at", "timestamp", "date")
                    ),
                    index=int(p.get("order", 0)),
                    role="assistant",
                    metadata={"group_id": group_id, "order": p.get("order", 0)},
                )
            )
            gold_memory_ids.append(pid)

        competency = first_present(row, "language", "competency")
        competency = (
            str(competency).lower() if competency is not None
            else "continual_learning"
        )

        metadata: dict[str, Any] = {"sequence": group_id, "position": order}
        for key in ("created_at", "difficulty", "version"):
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


def _parse_test_list(value: Any) -> list[str]:
    """Normalize FAIL_TO_PASS / PASS_TO_PASS into a list of test selectors."""
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                import json

                return [str(x) for x in json.loads(s)]
            except Exception:
                pass
        return [s] if s else []
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value]
    return [str(value)]


__all__ = ["SWEBenchCLLoader"]
