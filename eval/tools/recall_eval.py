#!/usr/bin/env python3
"""Tier-1 fast-eval: retrieval quality from a frozen recall dataset.

Reads ``recall_dataset.jsonl`` (from ``recall_harvest.py``) and reports, per
routing profile, the signals that nearly every memory change actually moves —
without running a solver:

* hit-score distribution (percentiles + a coarse histogram),
* **floor calibration**: for each candidate ``RECALL_MIN_SCORE`` floor, how many
  hits would be dropped and how many recalls would be starved to zero hits — the
  precision/recall trade the floor controls,
* optional ``--judge``: a one-shot LLM relevance label per (query, hit) pair,
  cached to disk, yielding precision@k / MRR against a real relevance signal.

The point: "did my recall change help?" becomes a sub-second diff instead of an
hours-long benchmark. Calibrate, then gate on a small smoke, then confirm with a
full run.

    python eval/tools/recall_eval.py eval/tools/recall_dataset.jsonl
    python eval/tools/recall_eval.py recall_dataset.jsonl --judge   # needs OPENROUTER_API_KEY
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

_FLOORS = [0.0, 0.05, 0.09, 0.10, 0.15, 0.20, 0.30]
_HIST_BINS = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 1.01]


def _load(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, int(round(p / 100.0 * (len(xs) - 1)))))
    return xs[i]


def _by_profile(rows: list[dict]) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(r.get("profile"), []).append(r)
    return out


def _report_profile(name, rows) -> None:
    n = len(rows)
    withhits = [r for r in rows if r.get("hits")]
    scores = [float(h["score"]) for r in rows for h in r["hits"] if h.get("score") is not None]
    floors_seen = sorted({r.get("min_score") for r in rows}, key=lambda x: (x is None, x))
    print(f"\n=== profile: {name!r} ===")
    print(f"  recalls: {n}  | with >=1 hit: {len(withhits)} ({100*len(withhits)//max(n,1)}%)"
          f"  | total hits: {len(scores)}  | floors in effect: {floors_seen}")
    if not scores:
        print("  (no scored hits)")
        return
    print(f"  hit-score  min={min(scores):.4f}  p10={_pct(scores,10):.4f}  "
          f"p50={_pct(scores,50):.4f}  p90={_pct(scores,90):.4f}  max={max(scores):.4f}")
    # histogram
    counts = [0] * (len(_HIST_BINS) - 1)
    for s in scores:
        for b in range(len(_HIST_BINS) - 1):
            if _HIST_BINS[b] <= s < _HIST_BINS[b + 1]:
                counts[b] += 1
                break
    print("  score histogram:")
    for b in range(len(counts)):
        bar = "#" * min(40, counts[b])
        print(f"    [{_HIST_BINS[b]:.2f},{_HIST_BINS[b+1]:.2f}) {counts[b]:4d} {bar}")
    # floor simulation
    print("  floor simulation (kept/dropped hits, recalls starved to 0):")
    for fl in _FLOORS:
        kept = sum(1 for s in scores if s >= fl)
        starved = sum(1 for r in withhits
                      if not any(float(h["score"]) >= fl for h in r["hits"] if h.get("score") is not None))
        print(f"    floor={fl:>4}:  keep {kept:4d}  drop {len(scores)-kept:4d}"
              f"  | starve {starved:3d}/{len(withhits)} recalls")


def _suggest_floor(rows: list[dict]) -> None:
    """Largest gap in the sorted score distribution = a clean bimodal split point."""
    scores = sorted(float(h["score"]) for r in rows for h in r["hits"]
                    if h.get("score") is not None)
    if len(scores) < 4:
        print("\n(too few scored hits to suggest a floor)")
        return
    best_gap, best_mid = 0.0, None
    for a, b in zip(scores, scores[1:]):
        if b - a > best_gap:
            best_gap, best_mid = b - a, (a + b) / 2
    print(f"\nSuggested floor ~ {best_mid:.3f} (largest score gap = {best_gap:.3f}); "
          f"set via RECALL_MIN_SCORE. Verify against a smoke run before trusting it.")


def _judge(rows, cache_path, model):
    """Optional: LLM relevance label per (query, hit). Cached; precision@k + MRR."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memeval"))
    from dreaming.llm import make_client  # type: ignore

    cache: dict = {}
    if os.path.exists(cache_path):
        for line in open(cache_path, encoding="utf-8"):
            d = json.loads(line)
            cache[d["key"]] = d["rel"]
    client = make_client(model=model)
    cf = open(cache_path, "a", encoding="utf-8")

    def rel(q, content):
        key = str(hash((q[:500], content[:500])))
        if key in cache:
            return cache[key]
        prompt = (f"Query (a coding task):\n{q[:1500]}\n\nMemory:\n{content[:800]}\n\n"
                  "Is this memory RELEVANT to solving the query? Answer exactly 1 or 0.")
        try:
            out = client.complete(prompt, system="You label retrieval relevance.", max_tokens=3)
            r = 1 if "1" in (out.text or "")[:5] else 0
        except Exception:
            r = 0
        cache[key] = r
        cf.write(json.dumps({"key": key, "rel": r}) + "\n")
        cf.flush()
        return r

    aps, mrrs, k_at = [], [], 5
    for r in rows:
        hits = r.get("hits") or []
        if not hits:
            continue
        labels = [rel(r["query"], h.get("content") or "") for h in hits[:k_at]]
        nrel = sum(labels)
        aps.append(nrel / min(len(labels), k_at))
        first = next((i for i, x in enumerate(labels) if x), None)
        mrrs.append(1.0 / (first + 1) if first is not None else 0.0)
    cf.close()
    if aps:
        print(f"\n=== judged retrieval (n={len(aps)} recalls, k={k_at}) ===")
        print(f"  precision@{k_at} = {sum(aps)/len(aps):.3f}   MRR = {sum(mrrs)/len(mrrs):.3f}")
    else:
        print("\n(no recalls with hits to judge)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tier-1 retrieval-quality eval.")
    ap.add_argument("dataset", nargs="?", default="eval/tools/recall_dataset.jsonl")
    ap.add_argument("--judge", action="store_true", help="LLM-label relevance (needs API key)")
    ap.add_argument("--judge-cache", default="eval/tools/recall_judge_cache.jsonl")
    ap.add_argument("--model", default=os.environ.get("DREAM_MODEL", "openrouter/auto"))
    args = ap.parse_args(argv)

    rows = _load(args.dataset)
    print(f"loaded {len(rows)} recalls from {args.dataset}")
    for name, rs in sorted(_by_profile(rows).items(), key=lambda kv: str(kv[0])):
        _report_profile(name, rs)
    _suggest_floor(rows)
    if args.judge:
        _judge(rows, args.judge_cache, args.model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
