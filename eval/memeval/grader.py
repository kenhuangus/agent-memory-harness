"""CODE-task grading — owner: Ken. Real patch-apply / test-run scoring.

The harness grades QA by normalized exact match (``metrics.qa_match``), but CODE
benchmarks (SWE-ContextBench, SWE-Bench-CL, ContextBench) need the model's patch
*applied in the repo* and the tests *run*. This module provides graders that slot
into ``harness.run(grader=...)`` / ``agent.run_agent(grader=...)`` — each a
``Callable[[Task, str], Optional[bool]]`` returning ``True`` (resolved),
``False`` (not resolved), or ``None`` (could not grade).

Graders
-------
* :class:`SWEBenchDockerGrader` — the **default for production CODE runs**. Wraps
  the official SWE-bench evaluation harness (per-task containers). A task is
  RESOLVED iff **every ``FAIL_TO_PASS`` test passes AND every ``PASS_TO_PASS``
  test still passes** after the patch is applied — the standard SWE-bench rule.
  Requires Docker + the optional ``swebench`` package (``pip install
  memeval[swebench]``); degrades per ``on_unavailable`` when either is missing.
* :func:`overlap_grader` — a cheap, dependency-free heuristic (token overlap of
  the prediction against the gold patch) for smoke tests / offline iteration.
  **Not** a substitute for real test execution; never report it as accuracy.

The pure pieces — :func:`build_prediction`, :func:`instance_id_of`,
:func:`resolved_from_report` — carry the SWE-bench contract and are unit-tested
without Docker. The container invocation is isolated in
:meth:`SWEBenchDockerGrader._evaluate`.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from .schema import Task, TaskKind

#: Default SWE-bench dataset whose Docker images back the instances. The coding
#: benchmarks derive from SWE-bench Verified, so its instance ids/images apply.
DEFAULT_DATASET = "princeton-nlp/SWE-bench_Verified"

Grader = Callable[[Task, str], Optional[bool]]


# --------------------------------------------------------------------------- #
# Pure helpers (no Docker, unit-tested)
# --------------------------------------------------------------------------- #
def instance_id_of(task: Task) -> str:
    """The SWE-bench instance id for ``task`` (metadata wins, else task_id)."""
    iid = task.metadata.get("instance_id")
    return str(iid) if iid else str(task.task_id)


def build_prediction(task: Task, prediction: str, *, model_name: str) -> dict:
    """Build one SWE-bench prediction record from a task + model output.

    SWE-bench expects ``{instance_id, model_name_or_path, model_patch}``. The
    ``prediction`` string is the model's unified diff (the agent returns it as
    its ``patch``); empty means "no patch produced".
    """
    return {
        "instance_id": instance_id_of(task),
        "model_name_or_path": model_name,
        "model_patch": prediction or "",
    }


def resolved_from_report(report: dict, instance_id: str) -> Optional[bool]:
    """Interpret a SWE-bench evaluation report for one instance.

    Robust across report shapes:

    * **Summary report** (swebench >= 2.x ``make_run_report`` output) — has
      ``resolved_ids`` / ``unresolved_ids`` / ``error_ids`` lists. Resolved iff
      the id is in ``resolved_ids``; ``False`` if it's in any not-resolved list;
      ``None`` if absent (not evaluated).
    * **Per-instance report** — ``{instance_id: {resolved | tests_status}}``;
      honors an explicit ``resolved`` boolean, else derives from ``tests_status``
      (all ``FAIL_TO_PASS`` and ``PASS_TO_PASS`` succeed, no failures).

    Returns ``None`` when the instance cannot be found (not graded).
    """
    # Summary shape first (the modern harness output).
    if "resolved_ids" in report:
        if instance_id in (report.get("resolved_ids") or []):
            return True
        for key in ("unresolved_ids", "error_ids", "incomplete_ids", "empty_patch_ids"):
            if instance_id in (report.get(key) or []):
                return False
        return None

    entry = report.get(instance_id)
    if entry is None:
        # Some reports nest under a top-level key; also accept a flat shape.
        entry = (report.get("instances") or {}).get(instance_id) if isinstance(
            report.get("instances"), dict
        ) else None
    if entry is None:
        return None
    if isinstance(entry, bool):
        return entry
    if "resolved" in entry:
        return bool(entry["resolved"])
    status = entry.get("tests_status")
    if not isinstance(status, dict):
        return None

    def _all_pass(group: str) -> bool:
        g = status.get(group) or {}
        success = list(g.get("success") or [])
        failure = list(g.get("failure") or [])
        return not failure and (group != "FAIL_TO_PASS" or len(success) > 0)

    return _all_pass("FAIL_TO_PASS") and _all_pass("PASS_TO_PASS")


# --------------------------------------------------------------------------- #
# Cheap offline grader
# --------------------------------------------------------------------------- #
def overlap_grader(task: Task, prediction: str, *, threshold: float = 0.5) -> Optional[bool]:
    """Token-overlap heuristic vs the gold patch. Smoke-test use only.

    Returns ``None`` for QA tasks or when no gold ``patch`` exists (nothing to
    compare). This does **not** run tests; it only signals "the prediction looks
    like the gold change" so the offline pipeline yields a non-trivial number.
    """
    if task.kind is not TaskKind.CODE or not task.patch:
        return None
    pred_tokens = set((prediction or "").split())
    gold_tokens = set(task.patch.split())
    if not pred_tokens or not gold_tokens:
        return False
    overlap = len(pred_tokens & gold_tokens) / len(pred_tokens | gold_tokens)
    return overlap >= threshold


# --------------------------------------------------------------------------- #
# Official SWE-bench Docker grader
# --------------------------------------------------------------------------- #
class SWEBenchDockerGrader:
    """Grade CODE tasks with the official SWE-bench harness (Docker).

    Per call: builds a one-instance prediction, runs the SWE-bench evaluation in
    its per-task container, and reads back the report to decide resolved/not.
    Heavy and slow (a container per task) but the only faithful CODE score.

    ``on_unavailable`` controls behavior when Docker or ``swebench`` is missing:
    ``"error"`` (default) raises a clear ``RuntimeError``; ``"skip"`` returns
    ``None`` (ungraded) so a run can proceed and report only what it could grade.
    """

    def __init__(
        self,
        *,
        dataset_name: str = DEFAULT_DATASET,
        model_name: str = "memeval",
        run_id: str = "memeval",
        timeout: int = 1800,
        max_workers: int = 1,
        on_unavailable: str = "error",
    ) -> None:
        if on_unavailable not in ("error", "skip"):
            raise ValueError("on_unavailable must be 'error' or 'skip'")
        self.dataset_name = dataset_name
        self.model_name = model_name
        self.run_id = run_id
        self.timeout = timeout
        self.max_workers = max_workers
        self.on_unavailable = on_unavailable

    def __call__(self, task: Task, prediction: str) -> Optional[bool]:
        if task.kind is not TaskKind.CODE:
            return None  # not a CODE task; let QA grading handle it
        try:
            report = self._evaluate(task, prediction)
        except _Unavailable as exc:
            if self.on_unavailable == "skip":
                return None
            raise RuntimeError(str(exc)) from exc
        return resolved_from_report(report, instance_id_of(task))

    # -- container invocation (isolated; not exercised offline) ------------- #
    def _evaluate(self, task: Task, prediction: str) -> dict:  # pragma: no cover
        """Run the SWE-bench harness for one instance and return its report.

        Isolated so the pure path stays testable. Lazy-imports ``swebench`` and
        invokes ``run_evaluation``; raises :class:`_Unavailable` if the package
        or Docker is not present.
        """
        try:
            from swebench.harness import run_evaluation as _re  # type: ignore
        except Exception as exc:
            raise _Unavailable(
                "SWE-bench grading requires the optional 'swebench' package and "
                "a running Docker daemon. Install with `pip install memeval[swebench]` "
                "and ensure Docker is available, or pass on_unavailable='skip'. "
                "Note: swebench is Linux-only (imports `resource`); on Windows run "
                "the eval from WSL."
            ) from exc

        import tempfile
        from pathlib import Path

        iid = instance_id_of(task)
        pred = build_prediction(task, prediction, model_name=self.model_name)
        with tempfile.TemporaryDirectory() as tmp:
            preds_path = Path(tmp) / "predictions.json"
            preds_path.write_text(json.dumps([pred]), encoding="utf-8")
            report_path = self._invoke(_re, iid, str(preds_path), tmp)
            data = json.loads(Path(report_path).read_text(encoding="utf-8"))
        return data

    def _invoke(self, _re: Any, iid: str, preds_path: str, report_dir: str):  # pragma: no cover
        """Call the swebench evaluation entry point across version differences.

        swebench >= 4.x exposes ``main`` (returns the summary-report path) with a
        long required-arg list; older releases expose ``run_evaluation``. Try the
        modern entry point first, then fall back.
        """
        if hasattr(_re, "main"):
            return _re.main(
                dataset_name=self.dataset_name,
                split="test",
                instance_ids=[iid],
                predictions_path=preds_path,
                max_workers=self.max_workers,
                force_rebuild=False,
                cache_level="env",
                clean=False,
                open_file_limit=4096,
                run_id=self.run_id,
                timeout=self.timeout,
                namespace=None,
                rewrite_reports=False,
                modal=False,
                report_dir=report_dir,
            )
        return _re.run_evaluation(  # older API
            dataset_name=self.dataset_name, split="test", instance_ids=[iid],
            predictions_path=preds_path, max_workers=self.max_workers,
            run_id=self.run_id, timeout=self.timeout,
        )


class _Unavailable(Exception):
    """Internal: swebench/Docker not available."""


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def get_grader(name: str, **kwargs: Any) -> Grader:
    """Resolve a grader by name.

    ``"swebench"`` / ``"docker"`` -> :class:`SWEBenchDockerGrader` (kwargs
    forwarded); ``"overlap"`` -> :func:`overlap_grader`; ``"none"`` -> a grader
    that always returns ``None`` (leave CODE ungraded).
    """
    key = (name or "").strip().lower()
    if key in ("swebench", "docker", "swebench-docker"):
        return SWEBenchDockerGrader(**kwargs)
    if key == "overlap":
        return lambda task, pred: overlap_grader(task, pred, **kwargs)
    if key in ("none", "", "off"):
        return lambda task, pred: None
    raise ValueError(f"unknown grader {name!r} (use swebench / overlap / none)")


__all__ = [
    "DEFAULT_DATASET",
    "Grader",
    "instance_id_of",
    "build_prediction",
    "resolved_from_report",
    "overlap_grader",
    "SWEBenchDockerGrader",
    "get_grader",
]
