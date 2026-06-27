"""Regression tests for CODE grading honesty/reporting.

These tests stay offline and focus on the accounting layer: ungraded tasks are
visible, but they are not folded into the accuracy denominator.
"""

from __future__ import annotations

import json
import types

from memeval.claudecode import pipeline as P
from memeval.claudecode.pipeline_summary import render_summary_md
from memeval.grader_swebench import _django_directives_from_patch_or_selectors
from memeval.loaders.swe_bench_cl import SWEBenchCLLoader
from memeval.trajectory import TrajectoryLogger, read_trajectory_list


def test_summary_uses_graded_denominator_for_resolved_cell() -> None:
    summary = {
        "benchmark": "swe_bench_cl",
        "pipeline": {
            "version": "vdjango",
            "sequence": "django_django_sequence",
            "harness": "cursor",
            "model": "composer-2.5",
            "n_tasks": 50,
            "n_stages": 1,
            "dream": {"provider": "openrouter", "model": "deepseek"},
            "grader": "swebench",
            "git_sha": "ce31ac0",
        },
        "stages": [{
            "stage": "base",
            "metrics": {
                "accuracy": 11 / 49,
                "relevancy": 0.0,
                "recency": 0.0,
                "efficiency": 0.0,
            },
            "resolved": 11,
            "graded_n": 49,
            "ungraded": 1,
            "n_tasks": 50,
            "cost_usd": 0.0,
            "grade_reasons": {
                "graded": 49,
                "get_test_directives yielded no test files from test_patch": 1,
            },
            "memory_health": {},
            "warnings": [],
        }],
        "deltas": {},
    }

    md = render_summary_md(summary)

    assert "| Stage | accuracy | relevancy | recency | efficiency | resolved (graded) | graded | n | cost |" in md
    assert "| base | 0.2245 | 0.0000 | 0.0000 | 0.0000 | 11/49 | 49 | 50 | $0.0000 |" in md
    assert "| base | 11/49 | 11/50 | 49 | 1 |" in md


def test_stage_warning_for_partial_grading() -> None:
    rr = types.SimpleNamespace(
        n_tasks=50,
        metadata={"graded_n": 49, "ungraded": 1},
    )

    warnings = P._stage_warnings("base", {"grader": "swebench"}, rr, {}, {}, {})

    assert warnings == [{
        "code": "partial_grading",
        "message": "1 of 50 tasks were ungraded; accuracy denominator is graded tasks only",
    }]


def test_trajectory_logger_can_record_explicit_ungraded_success(tmp_path) -> None:
    path = tmp_path / "traj.jsonl"
    log = TrajectoryLogger(path)
    opened = log.start_task("t1", model="m")
    opened.success = True

    finished = log.end_task(success=None, ended_at=1.0)
    log.close()

    assert finished.success is None
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["success"] is None
    assert read_trajectory_list(path)[0].success is None


def test_django_fixture_only_patch_gets_selector_directive_fallback() -> None:
    task = next(
        t for t in SWEBenchCLLoader().load(sequence="django_django_sequence", limit=None)
        if t.task_id == "django__django-10097"
    )

    directives = _django_directives_from_patch_or_selectors(task)

    assert directives == ["validators"]


def test_django_selector_fallback_preserves_bare_and_full_labels() -> None:
    # When no ``tests/<app>/...`` patch target exists, the recovery falls back to
    # the selectors. A bare module label (``validators.tests``) is itself a runnable
    # runtests directive and must NOT be discarded; a canonical unittest-form
    # selector keeps its full ``module.Class.method`` label. (CodeRabbit PR #225:
    # the old ``parts[:-2]`` / ``len < 3`` logic dropped bare labels and over-trimmed.)
    from memeval.schema import Benchmark, TaskKind
    from memeval.schema import Task

    task = Task(
        task_id="django__django-fixture-only",
        benchmark=Benchmark.SWE_BENCH_CL,
        kind=TaskKind.CODE,
        question="fixture-only patch",
        repo="django/django",
        test_patch="diff --git a/docs/x.txt b/docs/x.txt\n",  # no tests/<app>/ target
        fail_to_pass=["validators.tests"],
        pass_to_pass=["test_ok (validators.tests.TestCase)"],
    )

    directives = _django_directives_from_patch_or_selectors(task)

    assert directives == ["validators.tests", "validators.tests.TestCase.test_ok"]
