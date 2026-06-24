#!/usr/bin/env python3
"""Generate deterministic quality items and candidate cases for Track 0 scale retrieval."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[6]
EVAL_DIR = ROOT / "eval"
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from memeval.schema import MemoryItem
from memeval.stores.tests.scale_retrieval.helpers import (  # noqa: E402
    CHALLENGE,
    CONTROL,
    LENSES,
    ScaleCase,
    case_to_record,
    item_to_record,
    write_jsonl,
)


BASE_TS = 1_700_000_000.0


def _item(
    item_id: str,
    content: str,
    *,
    lens: str,
    fact_id: str,
    rare_key: str,
    anchor: str,
    timestamp: float,
    links: tuple[tuple[str, str], ...] = (),
    logical_id: str | None = None,
    version: int = 1,
) -> MemoryItem:
    metadata = {
        "scale_role": "quality",
        "lens": lens,
        "fact_id": fact_id,
        "rare_key": rare_key,
        "anchor": anchor,
        "okf_title": anchor,
        "okf_type": "Concept",
    }
    if links:
        metadata["okf_links"] = [[rel, target] for rel, target in links]
    if logical_id is not None:
        metadata["logical_id"] = logical_id
        metadata["valid_from"] = timestamp
    return MemoryItem(
        item_id=item_id,
        content=content,
        timestamp=timestamp,
        relevancy=1.0,
        session_id=f"scale-{lens}",
        source="scale_retrieval_quality",
        tags=["scale_retrieval", lens],
        version=version,
        metadata=metadata,
    )


def _case(
    case_id: str,
    lens: str,
    kind: str,
    query: str,
    gold: tuple[str, ...],
    *,
    target: str,
    gains: dict[str, float] | None = None,
    floor: str | None = None,
    floor_k: int = 10,
    as_of: float | None = None,
    distractors: tuple[str, ...] = (),
    calibration: dict | None = None,
) -> ScaleCase:
    return ScaleCase(
        case_id=case_id,
        lens=lens,
        kind=kind,
        query=query,
        as_of=as_of,
        gold_primary_ids=gold,
        gold_gains=gains or {gid: 1.0 for gid in gold},
        distractor_ids=distractors,
        target=target,
        floor=floor,
        floor_k=floor_k,
        calibration=calibration or {},
    )


def _lexical(i: int) -> tuple[list[MemoryItem], ScaleCase]:
    lens = "lexical"
    fact_id = f"fact-lex-{i:03d}"
    rare = f"rarelex{i:03d}"
    anchor = f"LexiconAnchor{i:03d}"
    item_id = f"lex-{i:03d}-gold"
    content = (
        f"{anchor} records the catalog marker {rare} for a museum ceramics drawer; "
        f"the verified glaze code is GLZ-{(i * 7 + 31) % 97:02d}."
    )
    item = _item(item_id, content, lens=lens, fact_id=fact_id, rare_key=rare,
                 anchor=anchor, timestamp=BASE_TS + i)
    query = f"{rare} verified glaze code"
    case = _case(
        f"lexical-{i:03d}",
        lens,
        CONTROL,
        query,
        (item_id,),
        target="backend_markdown",
        floor="backend_markdown",
    )
    return [item], case


def _semantic(i: int) -> tuple[list[MemoryItem], ScaleCase]:
    lens = "semantic_divergence"
    fact_id = f"fact-sem-{i:03d}"
    rare = f"raresem{i:03d}"
    anchor = f"SemanticAnchor{i:03d}"
    gold_id = f"sem-{i:03d}-gold"
    distractor_id = f"sem-{i:03d}-surface"
    gold = _item(
        gold_id,
        (
            f"{anchor} notes that the orchard crew activates irrigation from buried "
            f"moisture probes when root-zone readings drop below {10 + i % 7} percent. "
            f"The internal marker is {rare}."
        ),
        lens=lens,
        fact_id=fact_id,
        rare_key=rare,
        anchor=anchor,
        timestamp=BASE_TS + 1_000 + i,
    )
    distractor = _item(
        distractor_id,
        (
            f"Surface timer memo {i}: visible watering schedules and calendar timers "
            "are reviewed by the greenhouse shift lead, but no hidden dampness probes are used."
        ),
        lens=lens,
        fact_id=f"{fact_id}-distractor",
        rare_key=f"surfsem{i:03d}",
        anchor=f"SemanticDistractor{i:03d}",
        timestamp=BASE_TS + 1_100 + i,
    )
    if i % 6 == 1:
        query = f"{rare} buried moisture probes"
        target = "backend_vector_hash"
        floor = "backend_vector_hash"
        calibration = {"expect_drop": "trivial_floor"}
    else:
        # TODO(track0-task5): distinct discriminating per-case queries for the captained semantic lens.
        query = "Which note explains watering from hidden soil dampness sensors rather than a timer?"
        target = "accuracy_voyage"
        floor = "backend_vector_hash"
        calibration = {"expect_drop": "unsolved_target"}
    if i % 6 == 4:
        gold_ids = (f"sem-{i:03d}-missing",)
        calibration = {"expect_drop": "unknown_gold"}
    else:
        gold_ids = (gold_id,)
    case = _case(
        f"semantic-{i:03d}",
        lens,
        CHALLENGE,
        query,
        gold_ids,
        target=target,
        floor=floor,
        floor_k=10,
        distractors=(distractor_id,),
        calibration=calibration,
    )
    return [gold, distractor], case


def _multi_hop(i: int) -> tuple[list[MemoryItem], ScaleCase]:
    lens = "multi_hop_relational"
    fact_id = f"fact-hop-{i:03d}"
    rare = f"rarehop{i:03d}"
    a_id, b_id, c_id = f"hop-{i:03d}-a", f"hop-{i:03d}-b", f"hop-{i:03d}-c"
    anchor = f"HopAnchor{i:03d}"
    a = _item(
        a_id,
        f"{anchor} begins a field logistics chain and [calls]({b_id}.md) the middle relay.",
        lens=lens,
        fact_id=fact_id,
        rare_key=rare,
        anchor=anchor,
        timestamp=BASE_TS + 2_000 + i,
        links=(("calls", f"{b_id}.md"),),
    )
    b = _item(
        b_id,
        f"Middle relay {i} validates the cargo docket and [calls]({c_id}.md) the terminal depot.",
        lens=lens,
        fact_id=fact_id,
        rare_key=f"{rare}b",
        anchor=f"HopBridge{i:03d}",
        timestamp=BASE_TS + 2_100 + i,
        links=(("calls", f"{c_id}.md"),),
    )
    c = _item(
        c_id,
        f"Terminal depot {i} stores the final refrigerated crate allocation for marker {rare}.",
        lens=lens,
        fact_id=fact_id,
        rare_key=f"{rare}c",
        anchor=f"HopTerminal{i:03d}",
        timestamp=BASE_TS + 2_200 + i,
    )
    query = f"{anchor} callee"
    gold_ids = (c_id,)
    calibration = {}
    floor = "backend_markdown"
    target = "backend_graph_bfs"
    distractors: tuple[str, ...] = ()
    if i % 8 == 5:
        gold_ids = (f"hop-{i:03d}-missing",)
        calibration = {"expect_drop": "unknown_gold"}
    elif i % 8 == 6:
        query = f"{rare} terminal depot allocation"
        floor = "backend_markdown"
        calibration = {"expect_drop": "trivial_floor"}
    elif i % 8 == 7:
        distractors = (b_id,)
        # The bridge is intentionally ambiguous for a terminal-node question; drop if target ranks it.
        calibration = {"ambiguous_ids": [b_id], "expect_drop": "ambiguous_gold"}
    case = _case(
        f"multi-hop-{i:03d}",
        lens,
        CHALLENGE,
        query,
        gold_ids,
        target=target,
        floor=floor,
        floor_k=10,
        gains={c_id: 3.0, b_id: 1.0},
        distractors=distractors,
        calibration=calibration,
    )
    return [a, b, c], case


def _temporal(i: int) -> tuple[list[MemoryItem], ScaleCase]:
    lens = "temporal_versioned"
    fact_id = f"fact-temp-{i:03d}"
    rare = f"raretemp{i:03d}"
    logical_id = f"tide-ledger-{i:03d}"
    anchor = f"TideLedger{i:03d}"
    t1, t2, t3 = BASE_TS + 3_000 + i * 10, BASE_TS + 3_000 + i * 10 + 3, BASE_TS + 3_000 + i * 10 + 6
    v1 = _item(
        f"temp-{i:03d}-v1",
        f"{anchor} threshold status is five crates for harbor shelf checks; marker {rare}.",
        lens=lens,
        fact_id=fact_id,
        rare_key=f"{rare}a",
        anchor=anchor,
        timestamp=t1,
        logical_id=logical_id,
        version=1,
    )
    v2 = _item(
        f"temp-{i:03d}-v2",
        f"{anchor} threshold status is eight crates for harbor shelf checks; marker {rare}.",
        lens=lens,
        fact_id=fact_id,
        rare_key=f"{rare}b",
        anchor=anchor,
        timestamp=t2,
        logical_id=logical_id,
        version=2,
    )
    v3 = _item(
        f"temp-{i:03d}-v3",
        f"{anchor} threshold status is thirteen crates for harbor shelf checks; marker {rare}.",
        lens=lens,
        fact_id=fact_id,
        rare_key=f"{rare}c",
        anchor=anchor,
        timestamp=t3,
        logical_id=logical_id,
        version=3,
    )
    as_of = t2 + 1
    query = f"{anchor} threshold status harbor shelf"
    gold_ids = (v2.item_id,)
    calibration = {}
    if i % 7 == 4:
        as_of = t2 - 1
        calibration = {"expect_drop": "future_gold"}
    elif i % 7 == 5:
        query = f"{rare} eight crates harbor shelf"
        calibration = {"expect_drop": "trivial_floor"}
    case = _case(
        f"temporal-{i:03d}",
        lens,
        CHALLENGE,
        query,
        gold_ids,
        target="backend_markdown",
        floor="backend_markdown_unfiltered",
        floor_k=1,
        as_of=as_of,
        distractors=(v3.item_id,),
        calibration=calibration,
    )
    return [v1, v2, v3], case


def _mixed(i: int) -> tuple[list[MemoryItem], ScaleCase]:
    lens = "mixed_adversarial"
    fact_id = f"fact-mix-{i:03d}"
    rare = f"raremix{i:03d}"
    anchor = f"MixAnchor{i:03d}"
    root_id = f"mix-{i:03d}-root"
    policy_old_id = f"mix-{i:03d}-policy-old"
    policy_new_id = f"mix-{i:03d}-policy-new"
    t_old = BASE_TS + 4_000 + i * 10
    t_new = t_old + 7
    root = _item(
        root_id,
        (
            f"{anchor} controls the gallery access chain and [depends on]({policy_old_id}.md) "
            f"the approved policy while a future draft [depends on]({policy_new_id}.md) is staged."
        ),
        lens=lens,
        fact_id=fact_id,
        rare_key=rare,
        anchor=anchor,
        timestamp=t_old,
        links=(("depends on", f"{policy_old_id}.md"), ("depends on", f"{policy_new_id}.md")),
    )
    old = _item(
        policy_old_id,
        f"Approved gallery policy {i} keeps visitor badges amber before {anchor} expands.",
        lens=lens,
        fact_id=fact_id,
        rare_key=f"{rare}old",
        anchor=f"MixPolicyOld{i:03d}",
        timestamp=t_old + 1,
    )
    new = _item(
        policy_new_id,
        f"Future gallery policy {i} changes visitor badges to silver after the later expansion.",
        lens=lens,
        fact_id=fact_id,
        rare_key=f"{rare}new",
        anchor=f"MixPolicyNew{i:03d}",
        timestamp=t_new,
    )
    as_of = t_old + 2
    query = f"what does `{anchor}` depend on for gallery policy"
    gold_ids = (policy_old_id,)
    calibration = {}
    distractors = (policy_new_id,)
    if i % 8 == 4:
        gold_ids = (f"mix-{i:03d}-missing",)
        calibration = {"expect_drop": "unknown_gold"}
    elif i % 8 == 5:
        as_of = t_old
        calibration = {"expect_drop": "future_gold"}
    elif i % 8 == 6:
        query = f"{policy_old_id} approved gallery policy amber"
        calibration = {"expect_drop": "trivial_floor"}
    elif i % 8 == 7:
        distractors = (root_id,)
        # The root names both candidate policies; drop if target ranks it instead of the policy node.
        calibration = {"ambiguous_ids": [root_id], "expect_drop": "ambiguous_gold"}
    case = _case(
        f"mixed-{i:03d}",
        lens,
        CHALLENGE,
        query,
        gold_ids,
        target="router_cascade",
        floor="backend_markdown",
        floor_k=1,
        as_of=as_of,
        distractors=distractors,
        calibration=calibration,
    )
    return [root, old, new], case


BUILDERS = {
    "lexical": _lexical,
    "semantic_divergence": _semantic,
    "multi_hop_relational": _multi_hop,
    "temporal_versioned": _temporal,
    "mixed_adversarial": _mixed,
}


def generate(quality_count: int, case_count: int) -> tuple[list[MemoryItem], list[ScaleCase]]:
    per_lens = max(1, case_count // len(LENSES))
    remainder = max(0, case_count - per_lens * len(LENSES))
    items: list[MemoryItem] = []
    cases: list[ScaleCase] = []
    for lens_index, lens in enumerate(LENSES):
        n = per_lens + (1 if lens_index < remainder else 0)
        for i in range(n):
            new_items, case = BUILDERS[lens](i)
            items.extend(new_items)
            cases.append(case)
    while len(items) < quality_count:
        i = len(items)
        lens = "lexical"
        items.append(_item(
            f"quality-noise-{i:05d}",
            f"Neutral atlas note {i} records a public garden seating count and contains no case gold.",
            lens=lens,
            fact_id=f"fact-noise-{i:05d}",
            rare_key=f"raresize{i:05d}",
            anchor=f"NoiseAnchor{i:05d}",
            timestamp=BASE_TS + 9_000 + i,
        ))
    return items, cases


def _validate(items: list[MemoryItem], cases: list[ScaleCase]) -> None:
    item_ids = [item.item_id for item in items]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("duplicate quality item_id")
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("duplicate case_id")
    by_id = {item.item_id: item for item in items}
    for case in cases:
        if case.lens not in LENSES:
            raise ValueError(f"{case.case_id}: unknown lens {case.lens!r}")
        if case.kind == CONTROL and case.lens != "lexical":
            raise ValueError(f"{case.case_id}: only lexical cases are controls")
        for item_id in case.gold_primary_ids + case.distractor_ids:
            if item_id in by_id:
                continue
            if "missing" not in item_id:
                raise ValueError(f"{case.case_id}: unexpected dangling id {item_id}")
    for item in items:
        meta = item.metadata or {}
        if meta.get("okf_links") and "[" not in item.content:
            raise ValueError(f"{item.item_id}: okf_links must also exist in markdown body")
        if item.item_id.startswith("temp-") and "logical_id" not in meta:
            raise ValueError(f"{item.item_id}: temporal version missing logical_id")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quality-count", type=int, default=120)
    parser.add_argument("--case-count", type=int, default=60)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    items, cases = generate(args.quality_count, args.case_count)
    _validate(items, cases)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.out_dir / "quality_items.jsonl", [item_to_record(item) for item in items])
    write_jsonl(args.out_dir / "cases.generated.jsonl", [case_to_record(case) for case in cases])
    print(f"generated quality_items={len(items)} candidate_cases={len(cases)} out_dir={args.out_dir}")


if __name__ == "__main__":
    main()
