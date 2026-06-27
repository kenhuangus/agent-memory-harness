#!/usr/bin/env python3
"""Offline A/B for the dreamer's extraction prompt — Tier-2 of the improvement loop.

Runs the REAL extraction path (dreaming.extract_memories) on the SAME real session
transcripts under two prompt variants, then judges each variant's output with the same
transferable/actionable judge as the Tier-2 consolidation gate. This lets a new
extraction variant (e.g. V6) be approved or rejected vs the current one (V5) WITHOUT a
Tier-3 solver run — the whole point of the gated loop.

    python eval/tools/extract_ab.py --variants V5 V6 --sessions 3 --judge

Needs an LLM key (OpenRouter) for both extraction and judging.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "memeval"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "plugin"))

# real coding-agent transcripts produced by prior plugin-real benchmark runs
_DEFAULT_GLOB = os.path.join(
    _HERE, "..", ".claude-sandbox", "projects", "*plugin-real*", "*.jsonl"
)


def _load_chunks(pattern: str, n: int, max_chars: int):
    paths = sorted(glob.glob(pattern), key=lambda p: -os.path.getsize(p))[:n]
    chunks = []
    for p in paths:
        with open(p, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
        if text.strip():
            chunks.append((os.path.basename(p), text[:max_chars]))
    return chunks


def _extract(variant, chunks, client):
    from memeval.dreaming._extract import extract_memories
    from memeval.dreaming.redaction import redact

    os.environ["DREAM_EXTRACTION_VARIANT"] = variant
    counter = {"n": 0}

    def id_gen():
        counter["n"] += 1
        return f"{variant}-{counter['n']:04d}"

    out = []
    for sid, raw in chunks:
        red = redact(raw)
        items = extract_memories(red, client=client, session_id=f"ab-{sid}",
                                 now=1.0, id_gen=id_gen, max_tokens=2048)
        if items:
            out.extend(items)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tier-2 extraction A/B.")
    ap.add_argument("--variants", nargs="+", default=["V5", "V6"])
    ap.add_argument("--glob", default=_DEFAULT_GLOB)
    ap.add_argument("--sessions", type=int, default=3)
    ap.add_argument("--max-chars", type=int, default=12000)
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--model", default=os.environ.get("DREAM_MODEL", "openrouter/auto"),
                    help="extraction model (capable)")
    ap.add_argument("--judge-model", default="openai/gpt-4o-mini",
                    help="judge model — PINNED + deterministic to avoid auto-routing variance")
    args = ap.parse_args(argv)

    from memeval.dreaming.llm import make_client
    client = make_client(model=args.model)
    judge_client = make_client(model=args.judge_model) if args.judge else None
    chunks = _load_chunks(args.glob, args.sessions, args.max_chars)
    print(f"input: {len(chunks)} session chunks ({args.max_chars} chars each) from {args.glob}")
    if not chunks:
        print("NO INPUT CHUNKS FOUND — adjust --glob"); return 2

    judge = None
    if args.judge:
        from consolidation_gate import _judge as _cg_judge  # reuse Tier-2 judge
        judge = _cg_judge

    for v in args.variants:
        items = _extract(v, chunks, client)
        types: dict = {}
        for it in items:
            t = (getattr(it, "metadata", {}) or {}).get("okf_type", "?")
            types[t] = types.get(t, 0) + 1
        print(f"\n=== variant {v} ===")
        print(f"  yield: {len(items)} memories | okf_type: {types}")
        if judge and items:
            tr = sum(judge(judge_client, it.content)["transferable"] for it in items)
            ac = sum(judge(judge_client, it.content)["actionable"] for it in items)
            n = len(items)
            print(f"  transferable_fraction: {round(tr/n,3)}")
            print(f"  actionable_fraction:   {round(ac/n,3)}")
            print(f"  (per-session yield: {round(n/len(chunks),2)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
