#!/usr/bin/env python3
"""Tier-1 fast-eval harvester.

Scan benchmark run dirs for cookbook-memory ``recall`` events (ADR-harness-007)
and emit a normalized ``recall_dataset.jsonl`` — one row per recall, carrying the
query, the ranked hits with their scores, the routing profile, and the floor that
was in effect. This is the frozen input for ``recall_eval.py``, which measures
retrieval quality (score distribution, floor calibration, optional precision@k)
WITHOUT running a solver — turning the hours-long benchmark loop into seconds for
the whole class of recall/precision changes.

No external deps; reads the same ``events.jsonl`` files the harness already writes.

    python eval/tools/recall_harvest.py runs/ -o eval/tools/recall_dataset.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Iterator


def _iter_event_files(root: str) -> Iterator[str]:
    for dirpath, _dirs, files in os.walk(root):
        if "events.jsonl" in files:
            yield os.path.join(dirpath, "events.jsonl")


def _rows_from_file(path: str) -> Iterator[dict]:
    # run label = the path segment under runs/ (e.g. "sympy10-grok/plugin")
    run = path.replace("\\", "/")
    try:
        run = run.split("/runs/", 1)[1].rsplit("/.cookbook-memory", 1)[0]
        run = run.rsplit("/_memory", 1)[0]
    except IndexError:
        pass
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if '"recall"' not in line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("op") != "recall":
                continue
            meta = e.get("meta") or {}
            hits = [
                {
                    "id": h.get("id"),
                    "content": h.get("content"),
                    "score": h.get("score"),
                    "rank": h.get("rank"),
                }
                for h in (meta.get("hits") or [])
            ]
            yield {
                "run": run,
                "ts": e.get("ts"),
                "query": e.get("query") or "",
                "k": meta.get("k"),
                "n": meta.get("n", len(hits)),
                "profile": meta.get("profile"),
                "min_score": meta.get("min_score"),
                "hits": hits,
            }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Harvest recall events into a dataset.")
    ap.add_argument("root", nargs="?", default="runs", help="dir to scan (default: runs)")
    ap.add_argument("-o", "--out", default="eval/tools/recall_dataset.jsonl")
    args = ap.parse_args(argv)

    n_files = n_rows = n_withhits = 0
    profiles: dict = {}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as out:
        for f in _iter_event_files(args.root):
            n_files += 1
            for row in _rows_from_file(f):
                n_rows += 1
                if row["hits"]:
                    n_withhits += 1
                p = row["profile"]
                profiles[p] = profiles.get(p, 0) + 1
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(
        f"harvested {n_rows} recalls ({n_withhits} with >=1 hit) "
        f"from {n_files} event files -> {args.out}",
        file=sys.stderr,
    )
    print(f"profiles: {profiles}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
