#!/usr/bin/env python3
"""Track A offline A/B: route-to-one vs fuse-all vs fuse-all+rerank over the scale-eval retained cases.

Deterministic + offline (stdlib hashing embedder + lexical ``MockReranker``): measures the route-vs-fuse
MECHANISM + the lexical rerank reordering. The SEMANTIC rerank lift is the captained Voyage run (NOT here
— that needs MEMEVAL_LIVE + VOYAGE_API_KEY). Builds the three backends ONCE (shared across configs) over
the committed corpus, runs each config, and writes a captioned md + json to ``results/``.

Run: ``python3 eval/memeval/stores/tests/scale_retrieval/tools/fusion_rerank_ab.py``
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVAL_DIR = ROOT / "eval"
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from memeval.router import (  # noqa: E402
    GRAPH,
    MARKDOWN,
    VECTORS,
    Router,
    RouterStore,
    fusion_profile,
    speed_profile,
)
from memeval.stores import GraphStore, MarkdownStore, SqliteVectorStore  # noqa: E402
from memeval.stores.rerankers import MockReranker  # noqa: E402
from memeval.stores.tests.scale_retrieval.helpers import (  # noqa: E402
    load_cases,
    load_items,
    mrr_at_k,
    recall_at_k,
)

SR = Path(__file__).resolve().parents[1]  # the scale_retrieval/ package dir
KS = (1, 5, 10)


def _build_backends(items: list, tmp: Path) -> dict:
    backends = {
        MARKDOWN: MarkdownStore(tmp / "md"),
        VECTORS: SqliteVectorStore(str(tmp / "v.db")),
        GRAPH: GraphStore(path=str(tmp / "g.db")),
    }
    for item in items:
        for store in backends.values():
            store.write(item)
    return backends


def _mean(xs: list) -> float:
    return round(sum(xs) / len(xs), 4) if xs else 0.0


def run(quality_path: Path, filler_path: Path, cases_path: Path) -> dict:
    quality = load_items(quality_path)
    filler = load_items(filler_path) if filler_path.exists() else []
    cases = load_cases(cases_path)
    tmp = Path(tempfile.mkdtemp(prefix="track_a_ab_"))
    backends = _build_backends(quality + filler, tmp)
    configs = {
        "route_speed": RouterStore(Router.with_config(backends, speed_profile())),
        "fusion_rrf": RouterStore(Router.with_config(backends, fusion_profile(method="rrf", per_backend_k=50))),
        # The reranker rides in the PROFILE — read-orchestration is the router's domain; route() applies it,
        # no external RerankedStore wrapping at the call site.
        "fusion_rrf_rerank": RouterStore(Router.with_config(
            backends, fusion_profile(method="rrf", per_backend_k=50,
                                     reranker=MockReranker(), rerank_top_n=50))),
    }
    out: dict = {
        "mode": "offline", "embedder": "hashing", "reranker": "mock(lexical-jaccard)",
        "quality": len(quality), "filler": len(filler), "cases": len(cases), "configs": {},
    }
    for name, store in configs.items():
        agg = {f"recall@{k}": [] for k in KS}
        agg["MRR@10"] = []
        by_lens: dict = {}
        for case in cases:
            ids = [h.item_id for h in store.search(case.query, k=10, as_of=case.as_of)]
            for k in KS:
                agg[f"recall@{k}"].append(recall_at_k(ids, case.gold_primary_ids, k))
            m = mrr_at_k(ids, case.gold_primary_ids, 10)
            agg["MRR@10"].append(m)
            d = by_lens.setdefault(case.lens, {"recall@10": [], "MRR@10": []})
            d["recall@10"].append(recall_at_k(ids, case.gold_primary_ids, 10))
            d["MRR@10"].append(m)
        out["configs"][name] = {
            **{k: _mean(v) for k, v in agg.items()},
            "by_lens": {L: {kk: _mean(vv) for kk, vv in d.items()} for L, d in by_lens.items()},
        }
    return out


def render_md(out: dict) -> str:
    names = list(out["configs"])
    lenses = sorted({L for c in out["configs"].values() for L in c["by_lens"]})
    lines = [
        "# Track A — Route-to-one vs Fuse-all vs Fuse-all+Rerank",
        "",
        f"Caption: Offline deterministic A/B over the scale-eval retained cases (hashing embedder, lexical "
        f"MockReranker; quality={out['quality']}, filler={out['filler']}, cases={out['cases']}). Fusion fans "
        f"out to all backends and merges (RRF); rerank re-scores the fused top-50. The SEMANTIC rerank LIFT "
        f"requires the captained Voyage run; this measures the route-vs-fuse mechanism + lexical reordering "
        f"only.",
        "",
        "## Overall",
        "",
        "config | recall@1 | recall@5 | recall@10 | MRR@10",
        "--- | ---: | ---: | ---: | ---:",
    ]
    for name in names:
        d = out["configs"][name]
        lines.append(f"{name} | {d['recall@1']} | {d['recall@5']} | {d['recall@10']} | {d['MRR@10']}")
    lines += ["", "## By lens (recall@10 / MRR@10)", "", "lens | " + " | ".join(names),
              "--- | " + " | ".join("---:" for _ in names)]
    for L in lenses:
        row = [L]
        for name in names:
            dd = out["configs"][name]["by_lens"].get(L, {})
            row.append(f"{dd.get('recall@10', '—')}/{dd.get('MRR@10', '—')}")
        lines.append(" | ".join(row))
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quality", type=Path, default=SR / "quality_items.jsonl")
    ap.add_argument("--filler", type=Path, default=SR / "filler_items.jsonl")
    ap.add_argument("--cases", type=Path, default=SR / "cases.retained.jsonl")
    ap.add_argument("--out-md", type=Path, default=SR / "results" / "fusion_rerank_ab.md")
    ap.add_argument("--out-json", type=Path, default=SR / "results" / "fusion_rerank_ab.json")
    args = ap.parse_args()

    out = run(args.quality, args.filler, args.cases)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md = render_md(out)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
