"""Offline tests for the SWE-Bench-CL native evaluator.

Fully offline + deterministic: EchoAgent / EchoModel + per-group InMemoryStore +
the dependency-free ``overlap`` CODE grader (no Docker, no swebench, no network,
no heavy deps). Asserts the evaluator runs end-to-end over the bundled fixture
and that every native metric + component slice is computed and in range.

Run with the Windows Python:
    python -m pytest memeval/native/tests/test_native_swe_bench_cl.py
or standalone:
    python memeval/native/tests/test_native_swe_bench_cl.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make ``memeval`` importable when run as a standalone script from anywhere.
_EVAL_ROOT = Path(__file__).resolve().parents[3]  # .../eval
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

from memeval.loaders import get_loader  # noqa: E402
from memeval.schema import Benchmark, Task, TaskKind  # noqa: E402
from memeval.native import (  # noqa: E402
    BenchmarkNativeReport,
    DeterministicJudge,
    run_native,
)
from memeval.native.evaluators.swe_bench_cl import (  # noqa: E402
    SWEBenchCLNativeEvaluator,
)

_FIXTURES = _EVAL_ROOT / "tests" / "fixtures"
_FIXTURE = _FIXTURES / "swe_bench_cl.json"

# Every per-sequence metric the evaluator emits, with its valid range.
_SEQ_METRICS = {
    "accuracy": (0.0, 1.0),
    "forgetting": (-1.0, 1.0),
    "backward_transfer": (-1.0, 1.0),
    "forward_transfer": (-1.0, 1.0),
    "aulc": (0.0, 1.0),
    "cl_plasticity": (0.0, 1.0),
    "cl_stability": (-1.0, 2.0),  # 1 - F, F in [-1,1]
    "cl_f1": (0.0, 1.0),
    "cl_score": (-5.0, 5.0),  # unbounded in theory; finite-range sanity bound
}


def _load_tasks(limit=None):
    return get_loader(Benchmark.SWE_BENCH_CL).load(str(_FIXTURE), limit=limit)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_fixture_exists_and_loads() -> None:
    assert _FIXTURE.exists(), f"missing fixture {_FIXTURE}"
    tasks = _load_tasks()
    assert tasks, "fixture loaded zero tasks"
    for t in tasks:
        assert t.benchmark == Benchmark.SWE_BENCH_CL
        assert t.kind == TaskKind.CODE
        assert t.group_id  # grouped into a sequence
    # strictly orderable within the single fixture sequence
    orders = [t.order for t in tasks if t.group_id == "astropy_seq"]
    assert orders == sorted(orders)


def test_run_returns_three_phase_vectors() -> None:
    """run() emits initial-pass, final-state (retest), and mem-off records."""
    ev = SWEBenchCLNativeEvaluator()
    tasks = _load_tasks()
    records = ev.run(tasks, agent_or_model=None, mode="echo")
    phases = {r.extra.get("phase") for r in records}
    assert phases == {"initial_pass", "final_state", "mem_off"}
    # one record per task per phase
    n = len(tasks)
    for phase in ("initial_pass", "final_state", "mem_off"):
        assert sum(1 for r in records if r.extra.get("phase") == phase) == n
    # mem-off records carry memory_on=False; initial/final carry True
    on = [r for r in records if r.extra.get("phase") in ("initial_pass", "final_state")]
    off = [r for r in records if r.extra.get("phase") == "mem_off"]
    assert all(r.memory_on for r in on)
    assert all(not r.memory_on for r in off)


def test_run_retest_disabled_drops_final_phase() -> None:
    ev = SWEBenchCLNativeEvaluator()
    tasks = _load_tasks()
    records = ev.run(tasks, agent_or_model=None, mode="echo", retest=False)
    phases = {r.extra.get("phase") for r in records}
    assert "final_state" not in phases
    assert {"initial_pass", "mem_off"} <= phases


def test_score_headline_metrics_in_range() -> None:
    ev = SWEBenchCLNativeEvaluator()
    tasks = _load_tasks()
    records = ev.run(tasks, agent_or_model=None, mode="echo")
    report = ev.score(records, tasks)

    assert isinstance(report, BenchmarkNativeReport)
    assert report.benchmark == "swe_bench_cl"
    assert report.n_tasks >= 1
    # every headline CL metric present + in range
    for name, (lo, hi) in _SEQ_METRICS.items():
        m = report.metric(name)
        assert m is not None, f"missing headline metric {name}"
        assert lo <= m.value <= hi, f"{name}={m.value} out of [{lo},{hi}]"
        assert m.better in ("higher", "lower")
    # forgetting is the lower-is-better member
    assert report.metric("forgetting").better == "lower"
    # tool-use efficiency is the documented placeholder (0.0, N/A)
    tue = report.metric("tool_use_efficiency")
    assert tue is not None and tue.value == 0.0


def test_score_no_forgetting_offline() -> None:
    """Deterministic offline agent => final == initial => F = 0, BWT = 0, CL-S = 1."""
    ev = SWEBenchCLNativeEvaluator()
    tasks = _load_tasks()
    records = ev.run(tasks, agent_or_model=None, mode="echo")
    report = ev.score(records, tasks)
    assert report.metric("forgetting").value == 0.0
    assert report.metric("backward_transfer").value == 0.0
    assert report.metric("cl_stability").value == 1.0
    # ACC equals CL-plasticity when final == initial (offline determinism)
    assert report.metric("accuracy").value == report.metric("cl_plasticity").value


def test_components_per_sequence_and_strata() -> None:
    ev = SWEBenchCLNativeEvaluator()
    tasks = _load_tasks()
    records = ev.run(tasks, agent_or_model=None, mode="echo")
    report = ev.score(records, tasks)

    comps = report.components
    # per-sequence component (the fixture's single sequence)
    assert "astropy_seq" in comps
    seq = comps["astropy_seq"]
    assert seq.n == len(tasks)
    for name in _SEQ_METRICS:
        assert seq.get(name) is not None, f"sequence missing {name}"
    # snapshot-phase + memory-condition components present
    assert "snapshot_phase" in comps
    assert "memory_enabled" in comps
    assert "memory_disabled" in comps
    snap = comps["snapshot_phase"]
    assert snap.get("initial_pass_mean") is not None
    assert snap.get("final_state_mean") is not None
    # at least one difficulty stratum component (fixture has no difficulty -> unknown)
    assert any(name.startswith("difficulty_") for name in comps)


def test_run_native_end_to_end_offline() -> None:
    """Full offline runner path: loader + evaluator + EchoAgent + det judge."""
    report = run_native(
        Benchmark.SWE_BENCH_CL,
        model_or_agent=None,      # -> EchoAgent over EchoModel
        mode="echo",
        path_or_id=str(_FIXTURE),
        judge=DeterministicJudge(),
    )
    assert isinstance(report, BenchmarkNativeReport)
    assert report.benchmark == "swe_bench_cl"
    acc = report.metric("accuracy")
    assert acc is not None and 0.0 <= acc.value <= 1.0
    # provenance stamped by the runner
    assert report.metadata.get("judge") == "deterministic"
    assert report.metadata.get("paper") == "arXiv:2507.00014"
    # fully JSON-serializable (the CLI dumps this)
    json.dumps(report.to_dict())


def test_score_is_pure_repeatable() -> None:
    """score() is deterministic: same records -> identical report dict."""
    ev = SWEBenchCLNativeEvaluator()
    tasks = _load_tasks()
    records = ev.run(tasks, agent_or_model=None, mode="echo")
    a = ev.score(records, tasks).to_dict()
    b = ev.score(records, tasks).to_dict()
    assert a == b


def test_forward_transfer_synthetic() -> None:
    """FWT formula: mem-on minus mem-off on tasks 2..N, divided by N-1."""
    ev = SWEBenchCLNativeEvaluator()
    order = ["t0", "t1", "t2"]
    # mem-on solves t1,t2; mem-off solves neither of the later tasks.
    initial = {"t0": True, "t1": True, "t2": True}
    final = dict(initial)
    memoff = {"t0": True, "t1": False, "t2": False}
    m = ev._sequence_metrics(order, initial, final, memoff)
    # FWT = ((1-0) + (1-0)) / (N-1=2) = 1.0
    assert abs(m["forward_transfer"] - 1.0) < 1e-9
    # ACC over final-state = all solved = 1.0
    assert abs(m["accuracy"] - 1.0) < 1e-9
    # no forgetting (final == initial)
    assert m["forgetting"] == 0.0


def test_forgetting_and_bwt_synthetic() -> None:
    """When final-state degrades vs initial-pass, F>0 and BWT=-F."""
    ev = SWEBenchCLNativeEvaluator()
    order = ["t0", "t1", "t2"]
    initial = {"t0": True, "t1": True, "t2": True}
    # earlier task t0 regressed by end of sequence (forgotten).
    final = {"t0": False, "t1": True, "t2": True}
    memoff = {"t0": False, "t1": False, "t2": False}
    m = ev._sequence_metrics(order, initial, final, memoff)
    # F over first N-1 tasks {t0,t1}: (1-0)+(1-1) = 1, /2 = 0.5
    assert abs(m["forgetting"] - 0.5) < 1e-9
    assert abs(m["backward_transfer"] + 0.5) < 1e-9  # BWT = -F
    assert abs(m["cl_stability"] - 0.5) < 1e-9       # 1 - F


def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
