#!/usr/bin/env python3
"""Generate deterministic filler memories and assert they do not leak quality keys."""

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
    item_to_record,
    load_items,
    write_jsonl,
)


BASE_TS = 1_690_000_000.0
DOMAINS = (
    "public library shelving",
    "community garden rota",
    "train platform signage",
    "weather station maintenance",
    "cafeteria menu planning",
    "museum coat-check queue",
    "recreation center bookings",
    "harbor buoy inspection",
)


def _quality_needles(items: list[MemoryItem]) -> set[str]:
    needles: set[str] = set()
    for item in items:
        meta = item.metadata or {}
        for key in ("rare_key", "fact_id", "anchor"):
            value = str(meta.get(key, "")).strip().lower()
            if value:
                needles.add(value)
    return needles


def generate(count: int) -> list[MemoryItem]:
    items: list[MemoryItem] = []
    for i in range(count):
        domain = DOMAINS[i % len(DOMAINS)]
        item_id = f"sr-bulk-{i:05d}"
        link_text = ""
        links = []
        if i >= 17 and i % 17 == 0:
            target = f"sr-bulk-{i - 17:05d}.md"
            link_text = f" It is [related]({target}) to an earlier filler note."
            links = [["related", target]]
        content = (
            f"Bulk filler note {i:05d} describes {domain}; the routine count is "
            f"{(i * 37) % 251} and the public schedule bucket is {i % 11}.{link_text}"
        )
        metadata = {
            "scale_role": "filler",
            "okf_title": f"Bulk filler {i:05d}",
            "okf_type": "Concept",
        }
        if links:
            metadata["okf_links"] = links
        items.append(MemoryItem(
            item_id=item_id,
            content=content,
            timestamp=BASE_TS + i,
            relevancy=0.25,
            session_id="scale-filler",
            source="scale_retrieval_bulk",
            tags=["scale_retrieval", "filler"],
            metadata=metadata,
        ))
    return items


def assert_deleaked(filler: list[MemoryItem], quality: list[MemoryItem]) -> None:
    needles = _quality_needles(quality)
    for item in filler:
        text = f"{item.item_id} {item.content}".lower()
        for needle in needles:
            if needle and needle in text:
                raise ValueError(f"filler {item.item_id} leaks quality key {needle!r}")
        for rel, target in (item.metadata or {}).get("okf_links", []):
            if not str(target).startswith("sr-bulk-"):
                raise ValueError(f"filler {item.item_id} links outside filler corpus: {target!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=20_000)
    parser.add_argument("--quality", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    quality = load_items(args.quality)
    filler = generate(args.count)
    assert_deleaked(filler, quality)
    write_jsonl(args.out, [item_to_record(item) for item in filler])
    print(f"generated filler_items={len(filler)} out={args.out}")


if __name__ == "__main__":
    main()
