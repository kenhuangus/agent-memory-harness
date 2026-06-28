"""Focused tests for ui.aggregator.benchmark_manifest — specifically the
`solved` field's fallback path when graders write only the per-run
`runs[0].metrics.accuracy` aggregate and leave `tasks: []`.

Run from the repo root (needs `memeval` importable — the repo `.venv` after `make setup` —
and the repo root on PYTHONPATH so `ui` resolves):
    PYTHONPATH=. python -m pytest ui/tests/test_aggregator.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

from ui.aggregator import benchmark_manifest


def _write_run(
    root: Path,
    *,
    dirname: str,
    stage: str,
    sequence: str = "pydata_xarray_sequence",
    tasks: list | None = None,
    n_tasks: int = 22,
    accuracy: float | None = None,
) -> None:
    run_dir = root / dirname
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "pipeline": {
            "benchmark": "swe_bench_cl",
            "sequence": sequence,
            "stage": stage,
            "git_sha": "deadbee",
            "n_tasks": n_tasks,
            "timestamp": "20260625T184107Z",
            "model": "claude-haiku-4-5",
        },
        "dream": {"status": "not-run"},
        "runs": [
            {
                "n_tasks": n_tasks,
                "tasks": tasks if tasks is not None else [],
                "metrics": {"accuracy": accuracy} if accuracy is not None else {},
                "cost_usd": 0.0,
                "tokens_in": 0,
                "tokens_out": 0,
            }
        ],
    }
    (run_dir / "swe_bench_cl-20260625T184107Z.json").write_text(json.dumps(payload))


def test_solved_from_populated_tasks(tmp_path: Path) -> None:
    """The happy path — when per-task `resolved` flags exist, solved counts them."""
    _write_run(
        tmp_path,
        dirname="vpydata_xarray_sequence-plugin-blank-deadbee-1",
        stage="plugin-blank",
        tasks=[{"resolved": True}, {"resolved": False}, {"resolved": True}],
        n_tasks=3,
    )
    rows = benchmark_manifest(tmp_path, "pydata_xarray_sequence")
    assert len(rows) == 1
    assert rows[0]["solved"] == 2
    assert rows[0]["attempted"] == 3


def test_solved_falls_back_to_metrics_accuracy(tmp_path: Path) -> None:
    """Pydata-shape: tasks empty + metrics.accuracy populated → derive solved.

    accuracy=0.3181... × n_tasks=22 → round to 7, matching SUMMARY.md's "7/22".
    Without this fallback every chart panel that gates on `solved != None`
    renders blank for the entire pydata sub-tab.
    """
    _write_run(
        tmp_path,
        dirname="vpydata_xarray_sequence-plugin-blank-deadbee-1",
        stage="plugin-blank",
        tasks=[],
        n_tasks=22,
        accuracy=0.3181818181818182,
    )
    rows = benchmark_manifest(tmp_path, "pydata_xarray_sequence")
    assert len(rows) == 1
    assert rows[0]["solved"] == 7
    assert rows[0]["attempted"] == 22
    assert rows[0]["partial"] is False


def test_solved_stays_none_when_no_signal_available(tmp_path: Path) -> None:
    """Tasks empty AND metrics.accuracy missing → solved stays None (honest).

    Guards against a regression where the fallback hallucinates a count from
    an absent or non-numeric accuracy value.
    """
    _write_run(
        tmp_path,
        dirname="vpydata_xarray_sequence-base-deadbee-1",
        stage="base",
        tasks=[],
        n_tasks=22,
        accuracy=None,
    )
    rows = benchmark_manifest(tmp_path, "pydata_xarray_sequence")
    assert len(rows) == 1
    assert rows[0]["solved"] is None
