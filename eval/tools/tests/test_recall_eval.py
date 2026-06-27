"""Smallest runnable check for the Tier-1 fast-eval tools."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import recall_eval as re  # noqa: E402
import recall_harvest as rh  # noqa: E402


def _rows():
    return [
        {"profile": "fusion", "min_score": None,
         "hits": [{"score": 0.06, "content": "a"}, {"score": 0.01, "content": "b"}]},
        {"profile": "fusion", "min_score": None, "hits": []},
        {"profile": "accuracy", "min_score": 0.15,
         "hits": [{"score": 0.30, "content": "c"}]},
    ]


def test_by_profile_and_pct():
    bp = re._by_profile(_rows())
    assert set(bp) == {"fusion", "accuracy"}
    assert len(bp["fusion"]) == 2
    assert re._pct([0.0, 0.5, 1.0], 50) == 0.5


def test_suggest_floor_runs(capsys):
    re._suggest_floor(_rows())
    assert "floor" in capsys.readouterr().out.lower()


def test_harvest_parses_recall_event(tmp_path):
    f = tmp_path / "events.jsonl"
    f.write_text(json.dumps({
        "op": "recall", "ts": 1.0, "query": "fix evalf",
        "meta": {"k": 5, "n": 1, "profile": "fusion", "min_score": None,
                 "hits": [{"id": "m1", "content": "x", "score": 0.05, "rank": 0}]},
    }) + "\n" + json.dumps({"op": "remember", "ids": ["m1"]}) + "\n", encoding="utf-8")
    rows = list(rh._rows_from_file(str(f)))
    assert len(rows) == 1
    assert rows[0]["profile"] == "fusion"
    assert rows[0]["hits"][0]["score"] == 0.05
