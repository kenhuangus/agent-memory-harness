#!/usr/bin/env python3
"""Tier-2 consolidation-quality gate for the cookbook improvement loop.

Tier-1 (retrieval) can pass while solve rate still doesn't move, because a memory can
be *topically relevant* yet useless to act on ("sympy has a Matrix class" vs "clone the
queryset before mutating it in admin.py"). This gate judges the OUTPUT of the dreamer —
the stored memories — for the properties that actually transfer to a new task:

* transferable: a reusable Invariant/Convention/Fix/pattern, not a one-off restatement
  of a single task's prose.
* actionable: contains a concrete, applicable instruction (what to do / what to avoid),
  not a vague observation.
* well-formed: self-contained and specific (cheap proxy for faithfulness when the source
  session isn't on hand).

Reports transferable-fraction, actionable-fraction, yield, and an okf_type histogram, with
a PASS/FAIL against the loop thresholds. Compare a candidate dreamer/extraction change vs
the current baseline by pointing --store at each one's output.

    python eval/tools/consolidation_gate.py \
      --store results/vsympy_sympy_sequence-plugin-dreamed-8c48b84-1/_memory/.cookbook-memory \
      --judge --sample 60
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "memeval"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "plugin"))

_JUDGE_SYSTEM = (
    "You grade memories a coding agent stored for reuse on FUTURE tasks. "
    "Answer with a single character: 1 or 0. No other text."
)
_Q_TRANSFERABLE = (
    "Memory:\n{content}\n\n"
    "Is this a REUSABLE rule/pattern/convention/fix that would help on a DIFFERENT future "
    "task — as opposed to a one-off restatement of a single task's goal or a fact about one "
    "specific line/test? Answer 1 (reusable) or 0 (one-off). Answer only 1 or 0."
)
_Q_ACTIONABLE = (
    "Memory:\n{content}\n\n"
    "Does this tell you concretely what to DO or AVOID (an instruction you could act on), "
    "as opposed to a passive observation? Answer 1 (actionable) or 0 (observation). "
    "Answer only 1 or 0."
)


def _load_items(store_dir: str):
    from cookbook_memory.core.contract import build_store
    os.environ.setdefault("MEMORY_PROFILE", "fusion")
    return list(build_store(store_dir).all())


def _ask01(client, prompt: str) -> int:
    """One robust 1/0 question. Retries once on empty (auto-routed models sometimes
    return ''); parses the first 0/1 digit anywhere in the reply."""
    for _ in range(2):
        try:
            out = client.complete(prompt, system=_JUDGE_SYSTEM, max_tokens=8)
            txt = (out.text or "").strip()
            for ch in txt:
                if ch in "01":
                    return int(ch)
        except Exception:
            pass
    return 0


def _judge(client, content: str) -> dict:
    c = content[:900]
    return {"transferable": _ask01(client, _Q_TRANSFERABLE.format(content=c)),
            "actionable": _ask01(client, _Q_ACTIONABLE.format(content=c))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tier-2 consolidation-quality gate.")
    ap.add_argument("--store", required=True)
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--sample", type=int, default=0, help="judge first N items (0=all)")
    ap.add_argument("--model", default="openai/gpt-4o-mini")  # pinned deterministic judge
    ap.add_argument("--min-transferable", type=float, default=0.50)
    ap.add_argument("--min-actionable", type=float, default=0.50)
    ap.add_argument("--min-yield", type=int, default=1)
    args = ap.parse_args(argv)

    items = _load_items(args.store)
    types: dict = {}
    for it in items:
        t = (getattr(it, "metadata", {}) or {}).get("okf_type", "?")
        types[t] = types.get(t, 0) + 1
    print(f"yield: {len(items)} memories | okf_type histogram: {types}")

    if not args.judge:
        print("(run with --judge for transferable/actionable fractions)")
        return 0

    from dreaming.llm import make_client
    client = make_client(model=args.model)
    sample = items[: args.sample] if args.sample else items
    tr = ac = 0
    for it in sample:
        v = _judge(client, it.content)
        tr += v["transferable"]; ac += v["actionable"]
    n = len(sample)
    tr_frac = round(tr / n, 3) if n else 0.0
    ac_frac = round(ac / n, 3) if n else 0.0
    print(f"\njudged {n} memories:")
    print(f"  transferable_fraction: {tr_frac}")
    print(f"  actionable_fraction:   {ac_frac}")
    print("\n=== Tier-2 gate verdict ===")
    ok = (tr_frac >= args.min_transferable and ac_frac >= args.min_actionable
          and len(items) >= args.min_yield)
    print(f"  transferable>={args.min_transferable}: {tr_frac >= args.min_transferable}")
    print(f"  actionable>={args.min_actionable}:   {ac_frac >= args.min_actionable}")
    print(f"  yield>={args.min_yield}:            {len(items) >= args.min_yield}")
    print(f"  RESULT: {'PASS' if ok else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
