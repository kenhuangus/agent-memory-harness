#!/usr/bin/env python3
"""Tier-1 precision gate for the cookbook improvement loop.

Given a populated cookbook store and the real task queries that hit it, measure how
well a candidate embedder/profile RETRIEVES relevant memories — the necessary
precondition for any solve-rate lift. Compares profiles head-to-head so a code/config
change can be approved or rejected in minutes, before spending hours on Tier 3.

For each profile it: loads the stored MemoryItems, re-embeds them into a fresh vector
store under that profile's embedder (rebuild_store — you can't retrofit a new embedder
onto old vectors), recalls each query (k), and — with --judge — LLM-labels each hit's
relevance to its query, yielding precision@k / MRR / useful-hit-rate.

    python eval/tools/recall_precision_gate.py \
      --store results/vsympy_sympy_sequence-plugin-dreamed-8c48b84-1/_memory/.cookbook-memory \
      --queries results/vsympy_sympy_sequence-plugin-dreamed-8c48b84-1/_memory/.cookbook-memory/events.jsonl \
      --profiles fusion accuracy --k 5 --judge

Needs VOYAGE_API_KEY for the accuracy profile; --judge needs an LLM key (OpenRouter).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

# repo import paths
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "memeval"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "plugin"))


def _load_items(store_dir: str):
    """Return the MemoryItems in a cookbook store (content included)."""
    from cookbook_memory.core.contract import build_store
    # build under fusion to just READ items (no embedding needed to enumerate).
    os.environ.setdefault("MEMORY_PROFILE", "fusion")
    s = build_store(store_dir)
    return list(s.all())


def _queries(path: str, limit: int) -> list:
    out = []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if '"recall"' not in line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("op") == "recall" and (e.get("query") or "").strip():
                out.append(e["query"])
    # de-dup, preserve order
    seen, uniq = set(), []
    for q in out:
        if q not in seen:
            seen.add(q); uniq.append(q)
    return uniq[:limit] if limit else uniq


def _embedder_for(profile: str):
    from memeval.stores.embedders import VoyageEmbedder, SentenceTransformersEmbedder
    if profile == "accuracy":
        return VoyageEmbedder()
    if profile == "accuracy-local":
        return SentenceTransformersEmbedder()
    return None  # fusion/speed -> store default (offline hash)


def _judge_client(model: str):
    from dreaming.llm import make_client
    return make_client(model=model)


def _judge(client, query: str, content: str) -> int:
    prompt = (f"Query (a coding task):\n{query[:1500]}\n\nMemory:\n{content[:800]}\n\n"
              "Is this memory RELEVANT and potentially useful for solving the query? "
              "Answer exactly 1 (yes) or 0 (no).")
    try:
        out = client.complete(prompt, system="You label retrieval relevance for code tasks.",
                              max_tokens=3)
        return 1 if "1" in (out.text or "")[:5] else 0
    except Exception:
        return 0


def _run_profile(profile, items, queries, k, judge_client):
    from memeval.stores.embedders import rebuild_store
    id2content = {it.item_id: it.content for it in items}
    embed = _embedder_for(profile)
    dest = os.path.join(tempfile.mkdtemp(), "rebuilt.db")
    store = rebuild_store(items, dest, embed=embed,
                          embed_model=getattr(embed, "model", None) if embed else None)
    precisions, rrs, useful = [], [], 0
    for q in queries:
        hits = store.search(q, k=k)
        labels = []
        if judge_client is not None:
            labels = [_judge(judge_client, q, id2content.get(h.item_id, "")) for h in hits]
        scores = [getattr(h, "score", 0.0) for h in hits]
        if judge_client is not None and hits:
            nrel = sum(labels)
            precisions.append(nrel / min(len(hits), k))
            first = next((i for i, x in enumerate(labels) if x), None)
            rrs.append(1.0 / (first + 1) if first is not None else 0.0)
            if nrel > 0:
                useful += 1
        yield_top = scores[0] if scores else float("nan")
    n = len(queries)
    res = {
        "profile": profile, "n_queries": n,
        "top_score_mean": None,
    }
    if judge_client is not None:
        res["precision_at_k"] = round(sum(precisions) / len(precisions), 3) if precisions else 0.0
        res["mrr"] = round(sum(rrs) / len(rrs), 3) if rrs else 0.0
        res["useful_hit_rate"] = round(useful / n, 3) if n else 0.0
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tier-1 retrieval-precision gate.")
    ap.add_argument("--store", required=True)
    ap.add_argument("--queries", required=True)
    ap.add_argument("--profiles", nargs="+", default=["fusion", "accuracy"])
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0, help="max queries (0=all)")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--model", default="openai/gpt-4o-mini")  # pinned deterministic judge
    # gate thresholds (see docs/research/cookbook-improvement-loop.md)
    ap.add_argument("--min-useful-hit-rate", type=float, default=0.60)
    ap.add_argument("--min-precision", type=float, default=0.0)
    args = ap.parse_args(argv)

    items = _load_items(args.store)
    queries = _queries(args.queries, args.limit)
    print(f"store items: {len(items)} | queries: {len(queries)} | k={args.k} "
          f"| judge={'on' if args.judge else 'off'}")
    jc = _judge_client(args.model) if args.judge else None

    results = []
    for p in args.profiles:
        r = _run_profile(p, items, queries, args.k, jc)
        results.append(r)
        print(f"\n=== profile {p} ===")
        for key, val in r.items():
            if key != "profile":
                print(f"  {key}: {val}")

    if args.judge and len(results) >= 1:
        print("\n=== Tier-1 gate verdict ===")
        for r in results:
            ok = (r.get("useful_hit_rate", 0) >= args.min_useful_hit_rate
                  and r.get("precision_at_k", 0) >= args.min_precision)
            print(f"  {r['profile']}: useful_hit_rate={r.get('useful_hit_rate')} "
                  f"precision@{args.k}={r.get('precision_at_k')} -> "
                  f"{'PASS' if ok else 'FAIL'} (min useful≥{args.min_useful_hit_rate})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
