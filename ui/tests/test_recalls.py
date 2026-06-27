"""Tests for recall-telemetry aggregation (the Monitor "Recalls" sub-tab).

Pure read-side visualization of already-emitted ``op=="recall"`` events — these
tests assert the shape ``_aggregate_recalls`` / ``snapshot`` produce, never any
change to recall behavior.

Run from the repo root (same env as test_inspect.py):
    PYTHONPATH=. python -m pytest ui/tests/ -q
"""

from __future__ import annotations

import json
from pathlib import Path

from ui.aggregator import (
    DEFAULT_LOW_CONFIDENCE_FLOOR,
    RECALL_LIST_CAP,
    _aggregate_recalls,
    report_markdown,
    snapshot,
)
from ui.server import UIHandler, _State


def _recall_line(query, hits, *, ts=0.0, k=5, min_score=None, profile=None):
    """One on-disk recall event. Mirrors core/events.py: extra kwargs nest in ``meta``.
    ``min_score``/``profile`` mirror what contract.build_store stamps (PR #234)."""
    meta = {"k": k, "n": len(hits), "hits": hits}
    if min_score is not None:
        meta["min_score"] = min_score
    if profile is not None:
        meta["profile"] = profile
    return {
        "ts": ts,
        "op": "recall",
        "ids": [h["id"] for h in hits],
        "query": query,
        "meta": meta,
    }


def _hit(mem_id, score, rank, content="x"):
    return {"id": mem_id, "content": content, "score": score, "tokens": 10,
            "rank": rank, "timestamp": 1782446965.3}


def _write_events(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _events_with_recalls(tmp_path: Path) -> Path:
    """A representative events.jsonl: two scored recalls (one strong, one weak),
    one empty (n==0) recall, plus a non-recall op that must be ignored."""
    ev = tmp_path / "events.jsonl"
    records = [
        {"ts": 0.0, "op": "remember", "ids": ["mem_x"], "meta": {}},  # ignored
        _recall_line("fix evalf guard", [
            _hit("mem_3f2", 0.71, 0, "Guard evalf against None before formatting the result."),
            _hit("mem_91a", 0.55, 1, "The evalf fix lives in core/evalf.py near line 200."),
        ]),
        _recall_line("xarray merge", [
            _hit("mem_aa1", 0.22, 0, "merge() aligns coordinates before combining variables."),
            _hit("mem_bb2", 0.19, 1, "join='outer' is the default for xarray merge."),
        ]),
        _recall_line("nothing matches", []),  # empty / failed recall
    ]
    _write_events(ev, records)
    return ev


def test_aggregate_recalls_counts_and_stats(tmp_path):
    agg = _aggregate_recalls(_events_with_recalls(tmp_path))

    assert agg["count"] == 3              # three recall ops (remember ignored)
    assert agg["empty_count"] == 1        # the n==0 recall
    assert agg["k"] == 5                  # consistent k across events
    assert agg["truncated"] is False

    # avg_hits is the mean of n over NON-empty recalls: (2 + 2) / 2 == 2.0
    assert agg["avg_hits"] == 2.0

    # score_stats span every hit score across all recalls.
    stats = agg["score_stats"]
    assert stats["count"] == 4
    assert stats["min"] == 0.19
    assert stats["max"] == 0.71
    assert abs(stats["median"] - ((0.55 + 0.22) / 2)) < 1e-9
    assert len(stats["histogram"]) == 8           # fixed bucket count
    assert sum(stats["histogram"]) == 4           # every score lands in a bucket
    assert len(stats["bucket_edges"]) == 9


def test_aggregate_recalls_low_confidence_flag(tmp_path):
    agg = _aggregate_recalls(_events_with_recalls(tmp_path))

    assert agg["low_confidence_threshold"] == DEFAULT_LOW_CONFIDENCE_FLOOR  # 0.15, calibrated
    # Recalibration: the modest 'xarray merge' top (0.22) is a REAL match and is no longer
    # flagged under the calibrated 0.15 default (the old 0.30 guess wrongly flagged it).
    # Neither recall here is below 0.15, so nothing is flagged.
    assert agg["low_confidence_count"] == 0

    by_query = {r["query"]: r for r in agg["recalls"]}
    assert by_query["xarray merge"]["low_confidence"] is False     # 0.22 ≥ 0.15 → real match
    assert by_query["fix evalf guard"]["low_confidence"] is False
    # An empty recall is "empty", never flagged low-confidence.
    assert by_query["nothing matches"]["low_confidence"] is False


def test_low_confidence_uses_recorded_floor(tmp_path):
    """The ⚠ reference is the recall's RECORDED floor (meta.min_score, stamped by the live
    RECALL_MIN_SCORE knob), falling back to the calibrated default — so the view matches what
    the live system drops at. The recorded profile + floor are surfaced for observability."""
    ev = tmp_path / "events.jsonl"
    records = [
        # garbage recall, NO floor recorded → uses the 0.15 default; top 0.09 < 0.15 → ⚠.
        _recall_line("all garbage", [_hit("m1", 0.09, 0), _hit("m2", 0.04, 1)]),
        # floor recorded high (0.5) under accuracy; top 0.30 < 0.5 → ⚠ relative to ITS floor
        # (would NOT flag under the 0.15 default — proves the per-recall reference is used).
        _recall_line("near its floor", [_hit("m3", 0.30, 0)], min_score=0.5, profile="accuracy"),
    ]
    _write_events(ev, records)
    agg = _aggregate_recalls(ev)

    by_q = {r["query"]: r for r in agg["recalls"]}
    assert by_q["all garbage"]["low_confidence"] is True
    assert by_q["all garbage"]["applied_floor"] is None
    assert by_q["near its floor"]["low_confidence"] is True          # 0.30 < its recorded 0.5
    assert by_q["near its floor"]["applied_floor"] == 0.5
    assert by_q["near its floor"]["profile"] == "accuracy"
    # Aggregate surfaces the live floors + profiles actually recorded.
    assert agg["applied_floors"] == [0.5]
    assert agg["profiles"] == ["accuracy"]
    assert agg["low_confidence_threshold"] == DEFAULT_LOW_CONFIDENCE_FLOOR


def test_aggregate_recalls_list_shape_and_order(tmp_path):
    agg = _aggregate_recalls(_events_with_recalls(tmp_path))

    # newest-first: the empty recall was appended last.
    assert agg["recalls"][0]["query"] == "nothing matches"

    strong = next(r for r in agg["recalls"] if r["query"] == "fix evalf guard")
    assert strong["n"] == 2
    assert strong["k"] == 5
    assert strong["top_score"] == 0.71
    assert strong["min_score"] == 0.55
    assert strong["ids"] == ["mem_3f2", "mem_91a"]
    assert len(strong["hits"]) == 2
    hit0 = strong["hits"][0]
    assert set(hit0) == {"id", "score", "rank", "snippet"}
    assert hit0 == {"id": "mem_3f2", "score": 0.71, "rank": 0,
                    "snippet": "Guard evalf against None before formatting the result."}

    empty = next(r for r in agg["recalls"] if r["query"] == "nothing matches")
    assert empty["n"] == 0
    assert empty["top_score"] is None
    assert empty["min_score"] is None
    assert empty["hits"] == []


def test_aggregate_recalls_snippet_truncation(tmp_path):
    long = "y" * 400
    ev = tmp_path / "events.jsonl"
    _write_events(ev, [_recall_line("long", [_hit("mem_long", 0.9, 0, long)])])
    agg = _aggregate_recalls(ev)

    snip = agg["recalls"][0]["hits"][0]["snippet"]
    assert len(snip) <= 121          # 120 chars + the ellipsis
    assert snip.endswith("…")
    assert snip[:120] == "y" * 120


def test_aggregate_recalls_empty_file(tmp_path):
    agg = _aggregate_recalls(tmp_path / "missing.jsonl")
    assert agg["count"] == 0
    assert agg["empty_count"] == 0
    assert agg["avg_hits"] is None
    assert agg["k"] is None
    assert agg["low_confidence_count"] == 0
    assert agg["recalls"] == []
    assert agg["truncated"] is False
    assert agg["score_stats"]["count"] == 0
    assert agg["score_stats"]["median"] is None
    assert agg["score_stats"]["histogram"] == []


def test_aggregate_recalls_cap_and_truncation(tmp_path):
    ev = tmp_path / "events.jsonl"
    many = RECALL_LIST_CAP + 25
    records = [
        _recall_line(f"q{i}", [_hit(f"mem_{i}", 0.5, 0, f"content {i}")])
        for i in range(many)
    ]
    _write_events(ev, records)
    agg = _aggregate_recalls(ev)

    assert agg["count"] == many                 # full total still reported
    assert agg["truncated"] is True
    assert len(agg["recalls"]) == RECALL_LIST_CAP
    # newest-first: the last appended recall heads the capped list.
    assert agg["recalls"][0]["query"] == f"q{many - 1}"


def test_aggregate_recalls_inconsistent_k_is_none(tmp_path):
    ev = tmp_path / "events.jsonl"
    _write_events(ev, [
        _recall_line("a", [_hit("m1", 0.5, 0)], k=5),
        _recall_line("b", [_hit("m2", 0.5, 0)], k=10),
    ])
    assert _aggregate_recalls(ev)["k"] is None


# ---- snapshot integration ----------------------------------------------------


def _seed_run_dir(tmp_path: Path, recall_records) -> Path:
    """A minimal run dir whose basedir has only an events.jsonl with recalls."""
    run_dir = tmp_path / "vdemo_run-1"
    base = run_dir / "_memory" / ".cookbook-memory"
    _write_events(base / "events.jsonl", recall_records)
    return run_dir


def test_snapshot_includes_recalls(tmp_path):
    records = [
        _recall_line("query one", [_hit("mem_1", 0.8, 0, "alpha"), _hit("mem_2", 0.4, 1, "beta")]),
        _recall_line("query two", []),
    ]
    snap = snapshot(_seed_run_dir(tmp_path, records))

    assert "recalls" in snap
    rc = snap["recalls"]
    assert rc["count"] == 2
    assert rc["empty_count"] == 1
    assert len(rc["recalls"]) == 2
    assert rc["score_stats"]["count"] == 2

    # The recalls key is purely additive — the established snapshot keys are intact.
    for key in ("id", "metrics", "charts", "recent_memories", "reject_top", "pipeline"):
        assert key in snap


def test_snapshot_recalls_empty_when_no_recall_events(tmp_path):
    # events.jsonl exists but carries no recall ops -> well-formed empty aggregate.
    snap = snapshot(_seed_run_dir(tmp_path, [{"ts": 0.0, "op": "remember", "ids": [], "meta": {}}]))
    assert snap["recalls"]["count"] == 0
    assert snap["recalls"]["recalls"] == []


# ---- report export: report_markdown (Monitor "Print Report") -----------------


def _synthetic_snapshot() -> dict:
    """A full-shape snapshot dict exercising every report section: emit drift,
    failures + warnings, a low-confidence recall row, an empty recall, rejects,
    and a recent memory whose content needs pipe/newline escaping in a table."""
    return {
        "id": "vdjango_run-1",
        "label": "django · abc1234 · plugin",
        "basedir": "/tmp/x/_memory/.cookbook-memory",
        "as_of": 1782446965.0,
        "last_activity": {"ts": 1782446900.0, "age_s": 65.0, "is_active": True},
        "metrics": {
            "memory": {
                "memories_written": 12, "memories_from_store": 12,
                "memories_from_diary": 10, "emit_drift": True,
                "candidates_total": 20, "candidates_rejected": 8,
                "keep_rate": 0.6, "noise_filter_engaged": True,
            },
            "sessions": {
                "diaries": 5, "sidecars": 5, "locks": 0, "tasks_n": 10,
                "tasks_resolved": 4, "tasks_graded": 9, "tasks_ungraded": 1,
            },
            "cost": {"cost_usd": 1.2345, "tokens_in": 1000, "tokens_out": 2000, "budget_usd": 50},
            "failures": {
                "voyage_429": 2, "chunk_errors": 1, "hook_subprocess_failed": 0,
                "claude_timeouts": 3,
                "preflight_warnings": ["preflight: low disk"],
                "run_warnings": ["run: a task timed out"],
            },
        },
        "charts": {},
        "recent_memories": [
            {"ts": 1782446900.0, "session": "sess1234", "session_short": "sess1234",
             "content": "Guard evalf | with pipes\nand newlines", "tags": ["a", "b"],
             "relevancy": 0.9},
        ],
        "reject_top": [{"rationale": "too vague", "count": 5, "sample": "x"}],
        "reject_distinct": 3,
        "recalls": {
            "count": 2, "empty_count": 1, "avg_hits": 1.0, "k": 5,
            "low_confidence_count": 1, "low_confidence_threshold": 0.15,
            "score_stats": {
                "count": 1, "min": 0.22, "median": 0.22, "max": 0.22, "mean": 0.22,
                "histogram": [1, 0, 0, 0, 0, 0, 0, 0], "bucket_edges": [],
            },
            "recalls": [
                {"query": "xarray merge", "ts": 0.0, "n": 1, "k": 5, "top_score": 0.22,
                 "min_score": 0.22, "low_confidence": True, "ids": ["m1"],
                 "hits": [{"id": "m1", "score": 0.22, "rank": 0, "snippet": "x"}]},
                {"query": "nothing", "ts": 0.0, "n": 0, "k": 5, "top_score": None,
                 "min_score": None, "low_confidence": False, "ids": [], "hits": []},
            ],
            "truncated": False,
        },
        "pipeline": {"benchmark": "swe", "sequence": "django", "stage": "plugin",
                     "model": "claude", "git_sha": "abc1234", "version": "1", "n_tasks": 10},
    }


def test_report_markdown_sections_and_recall_row():
    md = report_markdown(_synthetic_snapshot())
    assert md.strip()                              # non-empty
    assert md.endswith("\n")
    for header in ("# Memory run report", "## Pipeline", "## Memory health",
                   "## Sessions", "## Cost", "## Recall summary", "## Failures",
                   "## Top reject reasons", "## Recent memories"):
        assert header in md
    # recall summary contains the recall table header + a real recall row
    assert "| query | n | top score | ⚠ |" in md
    assert "xarray merge" in md
    assert "0.22" in md
    # memory health surfaces emit drift + keep rate
    assert "emit drift" in md
    assert "60.0%" in md
    # cost + sessions
    assert "$1.2345" in md
    assert "of 10 tasks" in md
    # failures + their warning bullets
    assert "voyage 429" in md
    assert "preflight: low disk" in md
    assert "run: a task timed out" in md
    # table cells escape pipes and collapse newlines (recent-memory content)
    assert "Guard evalf \\| with pipes and newlines" in md


def test_report_markdown_error_snapshot():
    md = report_markdown({"error": "no basedir under x", "id": "vbad"})
    assert "error" in md.lower()
    assert "vbad" in md


def test_report_markdown_from_real_snapshot(tmp_path):
    # A snapshot produced by snapshot() (sparse: only recalls present) still renders.
    run_dir = _seed_run_dir(tmp_path, [_recall_line("q1", [_hit("m", 0.8, 0, "alpha")])])
    md = report_markdown(snapshot(run_dir))
    assert "## Recall summary" in md
    assert "## Memory health" in md
    assert "q1" in md
    assert md.endswith("\n")


# ---- report export: server routes (thin wrappers over snapshot/report_markdown) --


class _ReportHandler(UIHandler):
    """Drive do_GET without a socket: capture _json (errors) and _download (files).

    Mirrors test_inspect.py's _CaptureHandler pattern but also intercepts the
    attachment download path the report routes use."""

    def __init__(self, path, results_root):
        self.path = path
        self.state = _State(None, results_root=results_root)
        self.json_payload = None
        self.json_code = None
        self.dl = None

    def _json(self, obj, code=200):
        self.json_payload = obj
        self.json_code = code

    def _download(self, data, ctype, filename, code=200):
        self.dl = {"data": data, "ctype": ctype, "filename": filename, "code": code}


def test_report_json_route_returns_snapshot_and_attachment(tmp_path):
    run_dir = _seed_run_dir(tmp_path, [
        _recall_line("query one", [_hit("mem_1", 0.8, 0, "alpha"), _hit("mem_2", 0.4, 1, "beta")]),
        _recall_line("query two", []),
    ])
    h = _ReportHandler(f"/api/run/{run_dir.name}/report.json", tmp_path)
    h.do_GET()

    assert h.dl is not None
    assert h.dl["ctype"].startswith("application/json")
    # attachment filename derives from the (validated) run id
    assert h.dl["filename"] == f"{run_dir.name}-report.json"
    payload = json.loads(h.dl["data"].decode("utf-8"))
    # the JSON report is the snapshot dict verbatim
    assert payload["id"] == run_dir.name
    assert payload["recalls"]["count"] == 2
    for key in ("metrics", "charts", "recalls", "reject_top", "pipeline"):
        assert key in payload


def test_report_md_route_returns_markdown_and_attachment(tmp_path):
    run_dir = _seed_run_dir(tmp_path, [_recall_line("evalf guard", [_hit("m", 0.8, 0, "a")])])
    h = _ReportHandler(f"/api/run/{run_dir.name}/report.md", tmp_path)
    h.do_GET()

    assert h.dl is not None
    assert h.dl["ctype"].startswith("text/markdown")
    assert h.dl["filename"] == f"{run_dir.name}-report.md"
    md = h.dl["data"].decode("utf-8")
    assert "## Memory health" in md
    assert "## Recall summary" in md
    assert "evalf guard" in md


def test_report_route_unknown_run_is_404(tmp_path):
    h = _ReportHandler("/api/run/does-not-exist/report.json", tmp_path)
    h.do_GET()
    assert h.json_code == 404
    assert h.dl is None       # no file was sent


def test_aggregate_recalls_fail_open_on_malformed_lines(tmp_path):
    """Valid JSON but semantically-malformed recall lines (non-dict meta, hits holding
    non-dict entries, a non-dict event) must DEGRADE — never crash snapshot()."""
    ev = tmp_path / "events.jsonl"
    records = [
        "not-an-object",                                        # non-dict event line
        {"op": "recall", "query": "bad meta", "meta": "oops"},  # non-dict meta
        {"op": "recall", "query": "bad hits", "meta": {"hits": ["bad", 3, None]}},  # hits not dicts
        {"op": "recall", "query": "mixed", "meta": {"n": 2, "k": 5, "hits": [
            {"id": "m1", "score": 0.4, "rank": 0, "content": "ok"},
            "garbage",                                          # one bad hit among good
        ]}},
        _recall_line("good one", [_hit("m2", 0.6, 0, "fine")]),
    ]
    _write_events(ev, records)
    agg = _aggregate_recalls(ev)  # must not raise
    # 4 recall events parsed (the non-dict line skipped); the good + mixed contribute hits.
    assert agg["count"] == 4
    queries = [r["query"] for r in agg["recalls"]]
    assert "good one" in queries and "mixed" in queries
    mixed = next(r for r in agg["recalls"] if r["query"] == "mixed")
    assert len(mixed["hits"]) == 1 and mixed["hits"][0]["id"] == "m1"  # garbage hit dropped


def test_snapshot_fail_open_on_malformed_event_line(tmp_path):
    """snapshot() must not crash on a non-dict JSON event line — _aggregate_harness runs
    first and also reads events.jsonl, so the fail-open guard must live in _iter_jsonl
    (the single boundary), covering ALL aggregators, not just _aggregate_recalls."""
    base = tmp_path / "_memory" / ".cookbook-memory"
    base.mkdir(parents=True)
    _write_events(base / "events.jsonl", [
        "not-an-object",                                          # non-dict -> must skip
        {"op": "recall", "query": "ok", "meta": {"n": 1, "k": 5,
            "hits": [{"id": "m1", "score": 0.5, "rank": 0, "content": "x"}]}},
    ])
    snap = snapshot(tmp_path)  # must not raise
    assert "error" not in snap
    assert snap["recalls"]["count"] == 1
