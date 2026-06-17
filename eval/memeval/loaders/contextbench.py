"""ContextBench loader -- in-task context *retrieval* quality.

Real source
-----------
* HuggingFace dataset: ``Contextbench/ContextBench`` (validated).
  - config ``default`` (~1,136 tasks) and ``contextbench_verified`` (500-task
    subset); a single ``train`` split. Pass ``verified=True`` for the subset.
* GitHub: ``EuniAI/ContextBench`` · arXiv: 2602.05892 · docs: euniai.github.io/ContextBench
* 1,136 issue-resolution tasks, 66 repos, 8 languages, each augmented with
  HUMAN-ANNOTATED gold contexts (file / block / line spans). It measures
  retrieval **recall, precision and efficiency** -- the signals our *relevancy*
  and *efficiency* metrics track -- rather than final patch success.

NOT the same as SWE-ContextBench (``jiayuanz3/SWEContextBench``, cross-task
memory reuse). ContextBench is *in-task* retrieval quality with gold spans.

Row schema
----------
``instance_id, original_inst_id, repo, repo_url, language, base_commit,
gold_context (JSON-encoded list of {file, start_line, end_line, content}),
patch, test_patch, problem_statement, f2p, p2p, source``.

Normalization
-------------
Each row becomes one :class:`Task` (``TaskKind.CODE``):

* ``question``        <- ``problem_statement`` (issue text / retrieval query).
* ``sessions``        <- one Session per gold-context span, so the memory store
                         has the gold spans to retrieve; span id ``file:start-end``.
* ``gold_memory_ids`` <- every gold-context span id (all spans are gold).
* ``group_id``        <- the repo (shared-repo context).
* ``competency``      <- the language (stratification key); ``source`` -> metadata.
* repo / base_commit / patch / test_patch / fail_to_pass(f2p) / pass_to_pass(p2p).

Offline parsing of a local JSON path / fixture is stdlib-only; the HF download
path lazily imports ``datasets``.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from ..schema import Benchmark, Session, Task, TaskKind
from .base import BaseLoader, first_present, to_epoch


class ContextBenchLoader(BaseLoader):
    """Loader for ContextBench (``Contextbench/ContextBench``)."""

    benchmark: Benchmark = Benchmark.CONTEXTBENCH
    default_source: str = "Contextbench/ContextBench"
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
        split: str = "train",
        verified: bool = False,
        name: Optional[str] = None,
        **kwargs: Any,
    ) -> list[Task]:
        """Load ContextBench from HuggingFace.

        Two configs share a single ``train`` split: ``default`` (full) and
        ``contextbench_verified`` (500-task subset). Pass ``verified=True`` (or
        ``name="contextbench_verified"``) for the subset. ``datasets`` is
        imported lazily by the base helper, so the offline path stays
        stdlib-only.
        """
        config = name or ("contextbench_verified" if verified else "default")
        rows = self._fetch_hf_rows(source, split="train", name=config, limit=limit)
        return self._parse_rows(rows, limit=limit)

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
            first_present(row, "instance_id", "id", "original_inst_id",
                          default=f"cb_{idx}")
        )
        question = str(
            first_present(row, "problem_statement", "question", "issue",
                          "prompt", default="")
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
            first_present(row, "f2p", "FAIL_TO_PASS", "fail_to_pass", default=[])
        )
        pass_to_pass = _parse_test_list(
            first_present(row, "p2p", "PASS_TO_PASS", "pass_to_pass", default=[])
        )

        ts = to_epoch(first_present(row, "created_at", "timestamp", "date"))
        sessions, gold_memory_ids = self._gold_sessions(row, ts)

        language = first_present(row, "language", "lang")
        competency = None if language is None else str(language).lower()

        metadata: dict[str, Any] = {}
        for key in ("source", "repo_url", "original_inst_id", "language"):
            if key in row and row[key] is not None:
                metadata[key] = row[key]

        return Task(
            task_id=task_id,
            benchmark=self.benchmark,
            kind=TaskKind.CODE,
            question=question,
            answer=None,
            sessions=sessions,
            gold_memory_ids=gold_memory_ids,
            group_id=repo,
            order=0,
            repo=repo,
            base_commit=base_commit,
            patch=patch,
            test_patch=test_patch,
            fail_to_pass=fail_to_pass,
            pass_to_pass=pass_to_pass,
            competency=competency,
            metadata=metadata,
        )

    def _gold_sessions(
        self, row: dict, ts: float
    ) -> tuple[list[Session], list[str]]:
        """Turn the ``gold_context`` spans into retrievable Sessions + gold ids.

        ``gold_context`` is a JSON-encoded list (or an already-parsed list) of
        span objects ``{file, start_line, end_line, content}``. Each span
        becomes a :class:`Session` whose id is ``file:start-end``; every span is
        gold by design, so all ids land in ``gold_memory_ids``.
        """
        raw = first_present(
            row, "gold_context", "gold_contexts", "context", "gold_spans",
            default=[],
        )
        if isinstance(raw, str):
            s = raw.strip()
            try:
                raw = json.loads(s) if s else []
            except Exception:
                raw = []
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, (list, tuple)):
            return [], []

        sessions: list[Session] = []
        gold_ids: list[str] = []
        for i, span in enumerate(raw):
            if not isinstance(span, dict):
                sid = f"span_{i}"
                body = str(span)
                md: dict[str, Any] = {}
            else:
                file = first_present(span, "file", "path", "filename",
                                     "file_path")
                start = first_present(span, "start_line", "start", "line_start")
                end = first_present(span, "end_line", "end", "line_end")
                content = first_present(span, "content", "text", "code",
                                        "snippet", default="")
                loc = f"{file}:{start}-{end}" if file is not None else None
                sid = str(first_present(span, "id", "span_id",
                                        default=loc or f"span_{i}"))
                body = str(content) if content else (
                    f"{file or 'span'} lines {start}-{end}"
                )
                md = {}
                if file is not None:
                    md["file"] = file
                if start is not None:
                    md["start_line"] = start
                if end is not None:
                    md["end_line"] = end
            sessions.append(
                Session(
                    session_id=sid,
                    content=body,
                    timestamp=ts,
                    index=i,
                    role="system",
                    metadata=md,
                )
            )
            gold_ids.append(sid)
        return sessions, gold_ids


def _parse_test_list(value: Any) -> list[str]:
    """Normalize an f2p / p2p field into a list of strings (list or JSON string)."""
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                return [str(x) for x in json.loads(s)]
            except Exception:
                pass
        return [s] if s else []
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value]
    return [str(value)]


__all__ = ["ContextBenchLoader"]
