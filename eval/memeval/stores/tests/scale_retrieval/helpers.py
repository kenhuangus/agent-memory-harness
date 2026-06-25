"""Shared scale-retrieval fixture loaders, metrics, and matrix-cell construction.

The helpers live with the committed smoke fixtures so the CI test and the scratch
generators use the same JSONL schema and metric math. Everything here is stdlib-only
apart from the harness modules themselves.
"""

from __future__ import annotations

import json
import math
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from time import perf_counter_ns
from typing import Any, Optional

from memeval.router import (
    CascadeConfig,
    Router,
    RouterConfig,
    RouterStore,
    SemanticRouterClassifier,
    accuracy_profile,
    fusion_profile,
    speed_profile,
    GRAPH,
    MARKDOWN,
    VECTORS,
)
from memeval.schema import MemoryItem
from memeval.stores.embedders import SentenceTransformersEmbedder, VoyageEmbedder
from memeval.stores.graph_store import GraphStore
from memeval.stores.markdown_store import MarkdownStore
from memeval.stores.rerankers import MockReranker, RerankedStore, VoyageReranker
from memeval.stores.sqlite_store import (
    SQLITE_VEC_ANN_OVERFETCH,
    SQLITE_VEC_DIM,
    SqliteVectorStore,
)


LENSES = (
    "lexical",
    "semantic_divergence",
    "multi_hop_relational",
    "temporal_versioned",
    "mixed_adversarial",
)

CONTROL = "control"
CHALLENGE = "challenge"
DROP_REASONS = (
    "unknown_gold",
    "future_gold",
    "trivial_floor",
    "unsolved_target",
    "ambiguous_gold",
    "floor_not_beaten",
)

CURRENT_CELL_NAMES = (
    "backend_markdown",
    "backend_vector_hash",
    "backend_graph_bfs",
    "router_speed",
    "router_cascade",
    "fusion_rrf",
    "fusion_score",
)

SKIP_CELL_NAMES = (
    "accuracy_voyage",
    "accuracy_voyage_rerank",
    "fuse_all_voyage_rerank_rrf",
    "future_vector_sqlite_vec",
    "fusion_local_ann",
    "fusion_local_ann_rerank",
    "future_vector_usearch_hnsw",
    "future_graph_neo4j_phase_b",
    "future_graph_falkordb",
    "future_lexical_fts5",
)

LOCAL_ANN_CELL_NAMES = (
    "future_vector_sqlite_vec",
    "fusion_local_ann",
    "fusion_local_ann_rerank",
)


@dataclass(frozen=True)
class ScaleCase:
    case_id: str
    lens: str
    kind: str
    query: str
    gold_primary_ids: tuple[str, ...]
    gold_gains: dict[str, float]
    target: str
    floor: Optional[str] = None
    floor_k: int = 10
    distractor_ids: tuple[str, ...] = ()
    as_of: Optional[float] = None
    calibration: dict[str, Any] = field(default_factory=dict)


@dataclass
class MatrixCell:
    name: str
    store: Any
    columns: dict[str, str]
    write_summary: dict[str, Any] = field(default_factory=dict)
    _closers: tuple[Any, ...] = ()

    def close(self) -> None:
        for store in self._closers:
            close = getattr(store, "close", None)
            if callable(close):
                close()


@dataclass(frozen=True)
class Skip:
    name: str
    reason: str
    columns: dict[str, str]


def fixture_dir() -> Path:
    return Path(__file__).resolve().parent


def _json_default(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"cannot JSON encode {type(value).__name__}")


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, default=_json_default) + "\n")


def item_to_record(item: MemoryItem) -> dict[str, Any]:
    return {
        "item_id": item.item_id,
        "content": item.content,
        "timestamp": item.timestamp,
        "relevancy": item.relevancy,
        "session_id": item.session_id,
        "source": item.source,
        "tags": list(item.tags),
        "tokens": item.tokens,
        "version": item.version,
        "metadata": item.metadata or {},
    }


def item_from_record(row: dict[str, Any]) -> MemoryItem:
    return MemoryItem(
        item_id=str(row["item_id"]),
        content=str(row.get("content", "")),
        timestamp=float(row.get("timestamp") or 0.0),
        relevancy=float(row.get("relevancy", 1.0)),
        session_id=row.get("session_id"),
        source=row.get("source"),
        tags=[str(x) for x in row.get("tags", [])],
        tokens=int(row.get("tokens", 0) or 0),
        version=int(row.get("version", 1) or 1),
        metadata=dict(row.get("metadata") or {}),
    )


def case_to_record(case: ScaleCase) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "lens": case.lens,
        "kind": case.kind,
        "query": case.query,
        "as_of": case.as_of,
        "gold_primary_ids": list(case.gold_primary_ids),
        "gold_gains": case.gold_gains,
        "distractor_ids": list(case.distractor_ids),
        "target": case.target,
        "floor": case.floor,
        "floor_k": case.floor_k,
        "calibration": case.calibration,
    }


def case_from_record(row: dict[str, Any]) -> ScaleCase:
    return ScaleCase(
        case_id=str(row["case_id"]),
        lens=str(row["lens"]),
        kind=str(row["kind"]),
        query=str(row["query"]),
        as_of=(None if row.get("as_of") is None else float(row["as_of"])),
        gold_primary_ids=tuple(str(x) for x in row.get("gold_primary_ids", ())),
        gold_gains={str(k): float(v) for k, v in (row.get("gold_gains") or {}).items()},
        distractor_ids=tuple(str(x) for x in row.get("distractor_ids", ())),
        target=str(row["target"]),
        floor=row.get("floor"),
        floor_k=int(row.get("floor_k", 10) or 10),
        calibration=dict(row.get("calibration") or {}),
    )


def load_items(path: str | Path) -> list[MemoryItem]:
    return [item_from_record(row) for row in read_jsonl(path)]


def load_cases(path: str | Path) -> list[ScaleCase]:
    return [case_from_record(row) for row in read_jsonl(path)]


def clone_item(item: MemoryItem) -> MemoryItem:
    return item_from_record(item_to_record(item))


def recall_at_k(ranked_ids: list[str], gold_primary_ids: tuple[str, ...], k: int) -> float:
    gold = set(gold_primary_ids)
    if not gold:
        return 0.0
    return len(gold & set(ranked_ids[: max(0, k)])) / len(gold)


def mrr_at_k(ranked_ids: list[str], gold_primary_ids: tuple[str, ...], k: int = 10) -> float:
    gold = set(gold_primary_ids)
    if not gold:
        return 0.0
    for rank, item_id in enumerate(ranked_ids[: max(0, k)], start=1):
        if item_id in gold:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked_ids: list[str], gold_gains: dict[str, float], k: int = 10) -> float:
    if not gold_gains or k <= 0:
        return 0.0

    def discounted(gains: list[float]) -> float:
        return sum(g / math.log2(rank + 2) for rank, g in enumerate(gains))

    actual = [float(gold_gains.get(item_id, 0.0)) for item_id in ranked_ids[:k]]
    ideal = sorted((float(v) for v in gold_gains.values()), reverse=True)[:k]
    denom = discounted(ideal)
    return discounted(actual) / denom if denom else 0.0


def metrics_for_ranked_ids(ranked_ids: list[str], case: ScaleCase) -> dict[str, float]:
    return {
        "recall@1": recall_at_k(ranked_ids, case.gold_primary_ids, 1),
        "recall@5": recall_at_k(ranked_ids, case.gold_primary_ids, 5),
        "recall@10": recall_at_k(ranked_ids, case.gold_primary_ids, 10),
        "MRR@10": mrr_at_k(ranked_ids, case.gold_primary_ids, 10),
        "nDCG@10": ndcg_at_k(ranked_ids, case.gold_gains, 10),
    }


def evaluate_case(store: Any, case: ScaleCase, *, k: int = 10) -> dict[str, Any]:
    start = perf_counter_ns()
    hits = store.search(case.query, k=k, as_of=case.as_of)
    latency_ns = perf_counter_ns() - start
    ranked_ids = [h.item_id for h in hits]
    out = metrics_for_ranked_ids(ranked_ids, case)
    out.update({"ranked_ids": ranked_ids, "latency_ns": latency_ns})
    return out


def _columns(
    *,
    vector_index: str = "none",
    graph_engine: str = "none",
    lexical_engine: str = "none",
    reranker: str = "none",
) -> dict[str, str]:
    return {
        "vector_index": vector_index,
        "graph_engine": graph_engine,
        "lexical_engine": lexical_engine,
        "reranker": reranker,
    }


def _latency_summary(latencies: list[int], *, operation_count: int) -> dict[str, Any]:
    if not latencies:
        return {
            "operation_count": operation_count,
            "latency_p50_ns": None,
            "latency_p95_ns": None,
            "throughput_per_s": None,
        }
    ordered = sorted(latencies)
    p95_index = min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)
    total_ns = sum(ordered)
    throughput = (operation_count / (total_ns / 1_000_000_000.0)) if total_ns else None
    return {
        "operation_count": operation_count,
        "latency_p50_ns": int(median(ordered)),
        "latency_p95_ns": int(ordered[p95_index]),
        "throughput_per_s": throughput,
    }


def _write_items(stores: dict[str, Any], items: list[MemoryItem]) -> dict[str, dict[str, Any]]:
    latencies: dict[str, list[int]] = {name: [] for name in stores}
    combined: list[int] = []
    for item in items:
        for name, store in stores.items():
            start = perf_counter_ns()
            store.write(clone_item(item))
            elapsed = perf_counter_ns() - start
            latencies[name].append(elapsed)
            combined.append(elapsed)
    out = {
        name: _latency_summary(values, operation_count=len(values))
        for name, values in latencies.items()
    }
    out["combined"] = _latency_summary(combined, operation_count=len(combined))
    out["item_count"] = {"operation_count": len(items)}
    return out


def _fresh_bundle(
    items: list[MemoryItem],
    root: Path,
    *,
    embed: Any = None,
    graph_embed: Any = None,
    graph_max_depth: int = 2,
    vector_kwargs: Optional[dict[str, Any]] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    markdown = MarkdownStore(root / "markdown")
    vector = SqliteVectorStore(
        str(root / "vector.db"), embed=embed, **(vector_kwargs or {})
    )
    graph = GraphStore(path=str(root / "graph.db"), max_depth=graph_max_depth, embed=graph_embed)
    backends = {MARKDOWN: markdown, VECTORS: vector, GRAPH: graph}
    write_summary = _write_items(backends, items)
    return backends, write_summary


def _local_ann_columns(*, reranker: str = "none") -> dict[str, str]:
    return _columns(vector_index="sqlite_vec", reranker=reranker)


def _local_ann_fusion_columns(*, reranker: str = "none") -> dict[str, str]:
    return _columns(
        vector_index="sqlite_vec",
        graph_engine="in_memory_bfs",
        lexical_engine="shared_bm25",
        reranker=reranker,
    )


def _local_ann_columns_for(name: str) -> dict[str, str]:
    if name == "fusion_local_ann":
        return _local_ann_fusion_columns()
    if name == "fusion_local_ann_rerank":
        return _local_ann_fusion_columns(reranker="mock")
    return _local_ann_columns()


def _local_ann_unavailable_reason() -> Optional[str]:
    if os.environ.get("MEMEVAL_LOCAL_ANN") != "1":
        return "opt-in: MEMEVAL_LOCAL_ANN=1 unset"
    embed = SentenceTransformersEmbedder()
    try:
        embed.embed("local ANN availability probe", input_type="query")
    except RuntimeError as exc:
        return str(exc)
    probe = SqliteVectorStore(
        ":memory:",
        embed=embed,
        dim=SQLITE_VEC_DIM,
        vector_index="sqlite_vec",
        ann_overfetch=SQLITE_VEC_ANN_OVERFETCH,
        exact_rerank=True,
    )
    try:
        if probe.vector_index != "sqlite_vec":
            return probe.vector_index_status
    finally:
        probe.close()
    return None


def _local_ann_skips(reason: str) -> list[Skip]:
    return [
        Skip(name, reason, _local_ann_columns_for(name))
        for name in LOCAL_ANN_CELL_NAMES
    ]


def _local_ann_vector_store(path: str, embed: SentenceTransformersEmbedder) -> SqliteVectorStore:
    return SqliteVectorStore(
        path,
        embed=embed,
        embed_model=embed.model,
        dim=SQLITE_VEC_DIM,
        vector_index="sqlite_vec",
        ann_overfetch=SQLITE_VEC_ANN_OVERFETCH,
        exact_rerank=True,
    )


def _close_stores(stores: Any) -> None:
    for store in stores:
        close = getattr(store, "close", None)
        if callable(close):
            close()


def _local_ann_vector_cell(items: list[MemoryItem], root: Path) -> MatrixCell | Skip:
    name = "future_vector_sqlite_vec"
    cell_root = root / name
    if cell_root.exists():
        shutil.rmtree(cell_root)
    cell_root.mkdir(parents=True, exist_ok=True)
    embed = SentenceTransformersEmbedder()
    vector = _local_ann_vector_store(str(cell_root / "vector.db"), embed)
    if vector.vector_index != "sqlite_vec":
        reason = vector.vector_index_status
        vector.close()
        return Skip(name, reason, _local_ann_columns())
    writes = _write_items({VECTORS: vector}, items)
    return MatrixCell(
        name,
        vector,
        _local_ann_columns(),
        writes[VECTORS],
        (vector,),
    )


def _local_ann_fusion_cell(
    name: str,
    items: list[MemoryItem],
    root: Path,
) -> MatrixCell | Skip:
    cell_root = root / name
    if cell_root.exists():
        shutil.rmtree(cell_root)
    cell_root.mkdir(parents=True, exist_ok=True)
    embed = SentenceTransformersEmbedder()
    markdown = MarkdownStore(cell_root / "markdown")
    vector = _local_ann_vector_store(str(cell_root / "vector.db"), embed)
    graph = GraphStore(path=str(cell_root / "graph.db"), max_depth=2)
    backends = {MARKDOWN: markdown, VECTORS: vector, GRAPH: graph}
    if vector.vector_index != "sqlite_vec":
        reason = vector.vector_index_status
        _close_stores(backends.values())
        return Skip(name, reason, _local_ann_columns_for(name))
    writes = _write_items(backends, items)
    reranker = MockReranker() if name == "fusion_local_ann_rerank" else None
    config = fusion_profile(
        method="rrf",
        per_backend_k=50,
        rrf_k=60,
        reranker=reranker,
        rerank_top_n=50,
    )
    return MatrixCell(
        name,
        RouterStore(Router.with_config(backends, config)),
        _local_ann_columns_for(name),
        writes["combined"],
        tuple(backends.values()),
    )


def local_ann_cell(items: list[MemoryItem], root: Path) -> MatrixCell | Skip:
    cells = local_ann_cells(items, root, names=("future_vector_sqlite_vec",))
    return cells[0]


def local_ann_cells(
    items: list[MemoryItem],
    root: str | Path,
    *,
    include_skips: bool = True,
    names: tuple[str, ...] = LOCAL_ANN_CELL_NAMES,
) -> list[MatrixCell | Skip]:
    reason = _local_ann_unavailable_reason()
    if reason is not None:
        return [
            Skip(name, reason, _local_ann_columns_for(name))
            for name in names
        ] if include_skips else []
    root = Path(root)
    cells: list[MatrixCell | Skip] = []
    for name in names:
        if name == "future_vector_sqlite_vec":
            cell = _local_ann_vector_cell(items, root)
        elif name in {"fusion_local_ann", "fusion_local_ann_rerank"}:
            cell = _local_ann_fusion_cell(name, items, root)
        else:
            raise KeyError(name)
        if isinstance(cell, MatrixCell) or include_skips:
            cells.append(cell)
    return cells


def _current_cell(name: str, items: list[MemoryItem], root: Path) -> MatrixCell:
    if name == "backend_markdown":
        backends, writes = _fresh_bundle(items, root / name)
        return MatrixCell(
            name,
            backends[MARKDOWN],
            _columns(lexical_engine="shared_bm25"),
            writes[MARKDOWN],
            tuple(backends.values()),
        )
    if name == "backend_vector_hash":
        backends, writes = _fresh_bundle(items, root / name)
        return MatrixCell(
            name,
            backends[VECTORS],
            _columns(vector_index="brute_force"),
            writes[VECTORS],
            tuple(backends.values()),
        )
    if name == "backend_graph_bfs":
        backends, writes = _fresh_bundle(items, root / name)
        return MatrixCell(
            name,
            backends[GRAPH],
            _columns(graph_engine="in_memory_bfs"),
            writes[GRAPH],
            tuple(backends.values()),
        )
    if name == "router_speed":
        backends, writes = _fresh_bundle(items, root / name)
        router = Router.with_config(backends, speed_profile())
        return MatrixCell(
            name,
            RouterStore(router),
            _columns(
                vector_index="brute_force",
                graph_engine="in_memory_bfs",
                lexical_engine="shared_bm25",
            ),
            writes["combined"],
            tuple(backends.values()),
        )
    if name == "router_cascade":
        backends, writes = _fresh_bundle(items, root / name, graph_max_depth=3)
        config = RouterConfig(
            profile_name="cascade",
            cascade=CascadeConfig(enabled=True, graph_max_depth=3),
        )
        return MatrixCell(
            name,
            RouterStore(Router.with_config(backends, config)),
            _columns(
                vector_index="brute_force",
                graph_engine="in_memory_bfs",
                lexical_engine="shared_bm25",
            ),
            writes["combined"],
            tuple(backends.values()),
        )
    if name == "fusion_rrf":
        backends, writes = _fresh_bundle(items, root / name)
        config = fusion_profile(method="rrf", per_backend_k=50, rrf_k=60)
        return MatrixCell(
            name,
            RouterStore(Router.with_config(backends, config)),
            _columns(
                vector_index="brute_force",
                graph_engine="in_memory_bfs",
                lexical_engine="shared_bm25",
            ),
            writes["combined"],
            tuple(backends.values()),
        )
    if name == "fusion_score":
        backends, writes = _fresh_bundle(items, root / name)
        config = fusion_profile(method="score", per_backend_k=50)
        return MatrixCell(
            name,
            RouterStore(Router.with_config(backends, config)),
            _columns(
                vector_index="brute_force",
                graph_engine="in_memory_bfs",
                lexical_engine="shared_bm25",
            ),
            writes["combined"],
            tuple(backends.values()),
        )
    raise KeyError(name)


def _voyage_cell(name: str, items: list[MemoryItem], root: Path) -> MatrixCell:
    embed = VoyageEmbedder()
    classifier = SemanticRouterClassifier(embed)
    if name == "accuracy_voyage":
        backends, writes = _fresh_bundle(items, root / name, embed=embed, graph_embed=embed, graph_max_depth=3)
        config = accuracy_profile(classifier=classifier, embed=embed, embed_model=embed.model)
        return MatrixCell(
            name,
            RouterStore(Router.with_config(backends, config)),
            _columns(
                vector_index="brute_force",
                graph_engine="in_memory_bfs",
                lexical_engine="shared_bm25",
            ),
            writes["combined"],
            tuple(backends.values()),
        )
    if name == "accuracy_voyage_rerank":
        backends, writes = _fresh_bundle(items, root / name, embed=embed, graph_embed=embed, graph_max_depth=3)
        config = accuracy_profile(classifier=classifier, embed=embed, embed_model=embed.model)
        inner = RouterStore(Router.with_config(backends, config))
        return MatrixCell(
            name,
            RerankedStore(inner, VoyageReranker(), rerank_top_n=50),
            _columns(
                vector_index="brute_force",
                graph_engine="in_memory_bfs",
                lexical_engine="shared_bm25",
                reranker="voyage",
            ),
            writes["combined"],
            tuple(backends.values()),
        )
    if name == "fuse_all_voyage_rerank_rrf":
        backends, writes = _fresh_bundle(items, root / name, embed=embed, graph_embed=embed, graph_max_depth=3)
        config = fusion_profile(method="rrf", per_backend_k=50, rrf_k=60)
        inner = RouterStore(Router.with_config(backends, config))
        return MatrixCell(
            name,
            RerankedStore(inner, VoyageReranker(), rerank_top_n=50),
            _columns(
                vector_index="brute_force",
                graph_engine="in_memory_bfs",
                lexical_engine="shared_bm25",
                reranker="voyage",
            ),
            writes["combined"],
            tuple(backends.values()),
        )
    raise KeyError(name)


def skip_cells() -> list[Skip]:
    skips = [
        Skip(
            "accuracy_voyage",
            "captained: MEMEVAL_LIVE unset",
            _columns(
                vector_index="brute_force",
                graph_engine="in_memory_bfs",
                lexical_engine="shared_bm25",
            ),
        ),
        Skip(
            "accuracy_voyage_rerank",
            "captained: MEMEVAL_LIVE unset",
            _columns(
                vector_index="brute_force",
                graph_engine="in_memory_bfs",
                lexical_engine="shared_bm25",
                reranker="voyage",
            ),
        ),
        Skip(
            "fuse_all_voyage_rerank_rrf",
            "captained: MEMEVAL_LIVE unset",
            _columns(
                vector_index="brute_force",
                graph_engine="in_memory_bfs",
                lexical_engine="shared_bm25",
                reranker="voyage",
            ),
        ),
    ]
    local_reason = _local_ann_unavailable_reason()
    if local_reason is not None:
        skips.extend(_local_ann_skips(local_reason))
    skips.extend([
        Skip("future_vector_usearch_hnsw", "future: usearch_hnsw selector not implemented",
             _columns(vector_index="usearch_hnsw")),
        Skip("future_graph_neo4j_phase_b", "future: native Neo4j graph engine not in matrix yet",
             _columns(graph_engine="neo4j_phase_b")),
        Skip("future_graph_falkordb", "future: FalkorDB graph engine not in matrix yet",
             _columns(graph_engine="falkordb")),
        Skip("future_lexical_fts5", "future: SQLite FTS5 lexical engine not in matrix yet",
             _columns(lexical_engine="fts5")),
    ])
    return skips


def iter_matrix_cells(
    items: list[MemoryItem],
    root: str | Path,
    *,
    include_skips: bool = True,
    live: bool = False,
) -> list[MatrixCell | Skip]:
    root = Path(root)
    cells: list[MatrixCell | Skip] = [_current_cell(name, items, root) for name in CURRENT_CELL_NAMES]
    if live:
        missing = []
        if os.environ.get("MEMEVAL_LIVE") != "1":
            missing.append("MEMEVAL_LIVE=1")
        if not os.environ.get("VOYAGE_API_KEY"):
            missing.append("VOYAGE_API_KEY")
        if missing:
            cells.extend(
                Skip(skip.name, f"captained: {' and '.join(missing)} unset", skip.columns)
                for skip in skip_cells()[:3]
            )
        else:
            cells.extend(_voyage_cell(name, items, root) for name in SKIP_CELL_NAMES[:3])
    cells.extend(local_ann_cells(items, root, include_skips=include_skips))
    if include_skips:
        existing = {cell.name for cell in cells}
        cells.extend(skip for skip in skip_cells() if skip.name not in existing)
    return cells


def close_cells(cells: list[MatrixCell | Skip]) -> None:
    for cell in cells:
        if isinstance(cell, MatrixCell):
            cell.close()


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    metric_keys = ("recall@1", "recall@5", "recall@10", "MRR@10", "nDCG@10")
    latencies = sorted(int(r["latency_ns"]) for r in rows)
    p95_index = min(len(latencies) - 1, math.ceil(0.95 * len(latencies)) - 1)
    out = {key: sum(float(r[key]) for r in rows) / len(rows) for key in metric_keys}
    out.update({
        "n": len(rows),
        "latency_p50_ns": int(median(latencies)),
        "latency_p95_ns": int(latencies[p95_index]),
        "throughput_per_s": len(rows) / (sum(latencies) / 1_000_000_000.0) if sum(latencies) else None,
    })
    return out


def manifest_drop_table(manifest: dict[str, Any]) -> str:
    reasons = list(DROP_REASONS)
    lines = ["lens | generated | retained | dropped | " + " | ".join(reasons)]
    lines.append("--- | ---: | ---: | ---: | " + " | ".join("---:" for _ in reasons))
    for lens in LENSES:
        row = manifest["lenses"][lens]
        dropped_by_reason = row["dropped_by_reason"]
        lines.append(
            f"{lens} | {row['generated']} | {row['retained']} | {row['dropped']} | "
            + " | ".join(str(dropped_by_reason.get(reason, 0)) for reason in reasons)
        )
    total = manifest["totals"]
    dropped_by_reason = total["dropped_by_reason"]
    lines.append(
        f"TOTAL | {total['generated']} | {total['retained']} | {total['dropped']} | "
        + " | ".join(str(dropped_by_reason.get(reason, 0)) for reason in reasons)
    )
    return "\n".join(lines)


__all__ = [
    "CHALLENGE",
    "CONTROL",
    "CURRENT_CELL_NAMES",
    "DROP_REASONS",
    "LENSES",
    "LOCAL_ANN_CELL_NAMES",
    "MatrixCell",
    "ScaleCase",
    "Skip",
    "case_from_record",
    "case_to_record",
    "close_cells",
    "evaluate_case",
    "fixture_dir",
    "item_from_record",
    "item_to_record",
    "iter_matrix_cells",
    "load_cases",
    "load_items",
    "local_ann_cell",
    "local_ann_cells",
    "manifest_drop_table",
    "metrics_for_ranked_ids",
    "mrr_at_k",
    "ndcg_at_k",
    "read_jsonl",
    "recall_at_k",
    "skip_cells",
    "summarize_rows",
    "write_jsonl",
]
