"""Substrate adapter — open the three backends + Router/RouterStore over a store dir.

This is the read model behind the inspector UI. It mirrors the plugin's
``cookbook_memory/core/contract.py::build_store`` wiring (same profile selection,
same backend layout) but imports ONLY ``memeval`` — never the plugin — and is
strictly READ-ONLY over a real substrate: a backend whose on-disk artifact is
absent is replaced by an in-memory :class:`_EmptyStore` so the inspector never
creates files inside someone's ``results/`` directory.

Most views are derived through the stores' PUBLIC APIs only
(``all`` / ``get`` / ``search`` / ``classify`` / ``explain`` / ``write_plan``) without
reading a store's private internals. The one deliberate exception is
:meth:`Substrate.artifact_view`, which reads a memory's actual ``.md`` file (frontmatter +
body) for the backend-artifact popover — that view's whole purpose is "show the real file".
It still never parses ``.db`` files (vector/graph artifacts come from the store APIs plus
their on-disk paths). Graph relations are labelled via the public
:func:`memeval.stores.relations.classify_relation`.

Score semantics differ per backend and are NOT comparable across columns:
markdown is raw Okapi BM25 (non-negative, unbounded), vectors is cosine, graph is
token-overlap × hop-decay. The UI labels each column accordingly.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from memeval.router import (
    GRAPH,
    MARKDOWN,
    VECTORS,
    Router,
    RouterStore,
    accuracy_profile,
    fusion_profile,
    speed_profile,
)
from memeval.okf import _doc_relpath, split_doc
from memeval.schema import MemoryItem
from memeval.stores import GraphStore, MarkdownStore, SqliteVectorStore
from memeval.stores.relations import classify_relation

#: Backend names in canonical read / de-dup order (matches ``RouterStore._READ_ORDER``).
READ_ORDER = (MARKDOWN, VECTORS, GRAPH)

#: Per-backend on-disk artifact, relative to the store root, used to decide whether a
#: backend exists (so a missing one becomes a read-only empty adapter, never created).
_ARTIFACT = {
    MARKDOWN: "markdown",     # OKF bundle directory
    VECTORS: "memory.db",     # SqliteVectorStore file
    GRAPH: "graph.db",        # GraphStore durable mirror
}

#: Human-readable score semantics per backend (the columns are NOT comparable).
SCORE_SEMANTICS = {
    MARKDOWN: "BM25 (non-negative, unbounded)",
    VECTORS: "cosine similarity (-1..1, ~[0,1] in practice)",
    GRAPH: "token-overlap × hop-decay (0..1)",
}

#: Default ambiguity threshold, in rule-classifier score units (a single STRONG signal
#: is 3.0). A content margin below this means "no signal clearly won" → ambiguous.
DEFAULT_MARGIN_THRESHOLD = 1.0

#: The plugin nests its three backends under this subdir of the store path (see
#: ``eval/memeval/claudecode/checkout.py``: the plugin store lives at ``<root>/.cookbook-memory``).
#: A run's ``_memory`` dir therefore holds ``_memory/.cookbook-memory/{markdown,memory.db,graph.db}``,
#: so the inspector descends into it when the artifacts aren't directly under the given path.
_PLUGIN_SUBDIR = ".cookbook-memory"


def resolve_store_root(store_dir: str) -> Path:
    """Resolve the directory that actually holds the backends.

    Returns ``store_dir`` if a backend artifact sits directly under it; else the nested
    plugin store dir (``store_dir/.cookbook-memory``) if THAT holds artifacts; else
    ``store_dir`` unchanged (an empty substrate → empty adapters). One level of nesting
    only — enough for the plugin's layout without guessing."""
    root = Path(store_dir)
    if any((root / _ARTIFACT[n]).exists() for n in READ_ORDER):
        return root
    nested = root / _PLUGIN_SUBDIR
    if nested.is_dir() and any((nested / _ARTIFACT[n]).exists() for n in READ_ORDER):
        return nested
    return root


class _EmptyStore:
    """Read-only, file-free stand-in for a backend whose artifact is absent.

    Satisfies the read side of the ``MemoryStore`` protocol with empty results so the
    inspector can open a partial or empty substrate without constructing a real store
    (which would create ``memory.db`` / ``graph.db`` / a bundle dir on disk). Writes
    raise — the inspector never writes through a substrate it merely browses.
    """

    def write(self, item: MemoryItem) -> None:  # pragma: no cover - defensive
        raise RuntimeError("inspector substrate is read-only (empty adapter)")

    def get(self, item_id: str) -> Optional[MemoryItem]:
        return None

    def search(self, query: str, *, k: int = 5, as_of: Optional[float] = None, **kwargs: Any) -> list:
        return []

    def all(self) -> list:
        return []

    def delete(self, item_id: str) -> bool:  # pragma: no cover - defensive
        return False


def resolve_profile(profile: Optional[str]) -> tuple[str, str]:
    """Resolve a requested profile to ``(effective, source)``, mirroring ``build_store``.

    ``auto`` (or empty) reproduces the plugin's pick: ``$MEMORY_PROFILE`` if set, else
    ``accuracy`` when ``$VOYAGE_API_KEY`` is present, else ``fusion``. An explicit
    ``speed`` / ``fusion`` / ``accuracy`` is taken verbatim.
    """
    p = (profile or "auto").strip().lower()
    if p in ("", "auto"):
        env = (os.environ.get("MEMORY_PROFILE") or "").strip().lower()
        if env in ("speed", "fusion", "accuracy"):
            return env, f"auto→$MEMORY_PROFILE={env}"
        if os.environ.get("VOYAGE_API_KEY"):
            return "accuracy", "auto→accuracy ($VOYAGE_API_KEY present)"
        return "fusion", "auto→fusion (offline, no $VOYAGE_API_KEY)"
    if p not in ("speed", "fusion", "accuracy"):
        raise ValueError(f"unknown profile {profile!r}; choose speed|fusion|accuracy|auto")
    return p, "explicit"


@dataclass
class Substrate:
    """An opened, read-only view of a memory substrate behind the inspector.

    Holds the (possibly empty-adapter) backends, the configured Router + RouterStore
    engine, the resolved profile, and any construction warnings. All derived views are
    computed on demand through public store APIs.
    """

    store_dir: str
    backends: dict
    router: Router
    engine: RouterStore
    profile: str
    profile_source: str
    backend_status: dict           # name -> "ok" | "absent"
    warnings: list = field(default_factory=list)
    margin_threshold: float = DEFAULT_MARGIN_THRESHOLD

    # -- backend membership ------------------------------------------------
    def _id_sets(self) -> dict:
        """For each PRESENT backend, the set of item_ids it holds.

        Equivalent to ``backends[name].get(id) is not None`` (the actual-landing
        predicate) but computed once from ``all()`` instead of per-id, which is the same
        predicate at far lower cost on a large substrate.
        """
        return {name: {it.item_id for it in self.backends[name].all()}
                for name in READ_ORDER}

    def _present(self) -> list:
        return [n for n in READ_ORDER if self.backend_status.get(n) == "ok"]

    # -- canonical de-duped memory list ------------------------------------
    def _canonical(self) -> tuple[dict, list]:
        """``(id -> canonical MemoryItem, ordered ids)`` — union of ``all()`` across
        backends, de-duped by ``item_id``, canonical copy picked in ``READ_ORDER``
        (markdown first, matching ``RouterStore``)."""
        canon: dict = {}
        order: list = []
        for name in READ_ORDER:
            for it in self.backends[name].all():
                if it.item_id not in canon:
                    canon[it.item_id] = it
                    order.append(it.item_id)
        return canon, order

    def _edges_for(self, item_id: str, canonical: MemoryItem) -> list:
        """Typed graph edges for an item from its ``okf_links``.

        Read preferentially from the GRAPH copy, then VECTORS, then the canonical
        (markdown) copy: the markdown/OKF round-trip drops ``okf_links`` from
        frontmatter (they live in the body as links), so the graph/vector copies are the
        reliable carrier. Each ``(anchor, target)`` becomes ``{anchor, relation,
        target}`` with the relation labelled by the PUBLIC ``classify_relation``.
        """
        links = None
        for name in (GRAPH, VECTORS, MARKDOWN):
            store = self.backends[name]
            copy = store.get(item_id)
            if copy is not None:
                cand = (copy.metadata or {}).get("okf_links")
                if cand:
                    links = cand
                    break
        if links is None:
            links = (canonical.metadata or {}).get("okf_links") or []
        edges = []
        for entry in links:
            anchor, target = _split_link(entry)
            edges.append({
                "anchor": anchor,
                "relation": classify_relation(anchor),
                "target": _target_id(target),
            })
        return edges

    def _routing_for(self, item: MemoryItem, landing: list) -> dict:
        """Predicted vs actual routing for one memory (the routing-effectiveness core).

        PREDICTED: ``router.explain(content)`` (choice + per-backend scores + margin) and
        ``router.write_plan(item)`` (where the active write policy would persist it).
        ACTUAL: ``landing`` (the present backends that really hold this id). Under the
        ``base_all`` default the meaningful signal is the write_plan-vs-actual ASYMMETRY
        (a write that didn't fan out as policy intended) plus a low classify margin
        (ambiguous content) — NOT "the single backend is wrong".
        """
        content = item.content or ""
        ex = self.router.explain(content)
        plan = self.router.write_plan(item)
        present = self._present()
        plan_present = [n for n in plan if n in present]

        flag_reasons = []
        asymmetric = set(landing) != set(plan_present)
        if asymmetric:
            flag_reasons.append("write_plan≠actual (write did not fan out as policy intended)")
        ambiguous = ex["margin"] < self.margin_threshold
        if ambiguous:
            flag_reasons.append(f"low margin {ex['margin']:.2f} < {self.margin_threshold:g} (ambiguous)")

        human_intent = (item.metadata or {}).get("human_intent")
        intent_mismatch = bool(human_intent) and human_intent != ex["choice"]

        return {
            "classify": ex["choice"],
            "scores": ex["scores"],
            "margin": ex["margin"],
            "write_plan": plan,
            "write_plan_present": plan_present,
            "actual_landing": landing,
            "flagged": bool(flag_reasons),
            "flag_reasons": flag_reasons,
            "asymmetric": asymmetric,
            "ambiguous": ambiguous,
            "human_intent": human_intent,
            "intent_mismatch": intent_mismatch,
        }

    def memories(self) -> list:
        """Every unique memory (de-duped union of ``all()``), with membership, edges and
        routing-effectiveness fields. Flagged items are sorted to the top, then by id."""
        canon, order = self._canonical()
        id_sets = self._id_sets()
        rows = []
        for item_id in order:
            item = canon[item_id]
            landing = [n for n in READ_ORDER if item_id in id_sets.get(n, set())]
            meta = dict(item.metadata or {})
            rows.append({
                "item_id": item_id,
                "content": item.content,
                "snippet": _snippet(item.content),
                "tags": list(item.tags),
                "timestamp": item.timestamp,
                "version": item.version,
                "relevancy": item.relevancy,
                "source": item.source,
                "membership": {n: (item_id in id_sets.get(n, set())) for n in READ_ORDER},
                "okf": {
                    "title": meta.get("okf_title"),
                    "type": meta.get("okf_type"),
                    "resource": meta.get("okf_resource"),
                },
                "metadata": meta,
                "edges": self._edges_for(item_id, item),
                "routing": self._routing_for(item, landing),
            })
        rows.sort(key=lambda r: (not r["routing"]["flagged"], r["item_id"]))
        return rows

    def summary(self) -> dict:
        """Store path, active profile, per-backend counts, fan-out histogram, and the
        mis-route / ambiguity / flag counts for the summary strip."""
        rows = self.memories()
        histogram = {"1": 0, "2": 0, "3": 0}
        for r in rows:
            n = sum(1 for v in r["membership"].values() if v)
            if n:
                histogram[str(n)] = histogram.get(str(n), 0) + 1
        counts = {n: len(self.backends[n].all()) for n in READ_ORDER}
        misroute = sum(1 for r in rows if r["routing"]["asymmetric"])
        ambiguous = sum(1 for r in rows if r["routing"]["ambiguous"])
        flagged = sum(1 for r in rows if r["routing"]["flagged"])
        return {
            "store_path": self.store_dir,
            "profile": self.profile,
            "profile_source": self.profile_source,
            "backend_status": dict(self.backend_status),
            "counts": counts,
            "total_unique": len(rows),
            "fanout_histogram": histogram,
            "misroute_count": misroute,
            "ambiguous_count": ambiguous,
            "flagged_count": flagged,
            "intent_mismatch_count": sum(1 for r in rows if r["routing"]["intent_mismatch"]),
            "margin_threshold": self.margin_threshold,
            "score_semantics": dict(SCORE_SEMANTICS),
            "warnings": list(self.warnings),
        }

    def probe(self, query: str, k: int = 5) -> dict:
        """Run ``query`` through the routing decision, each backend RAW, and the routed
        engine. Per-backend score semantics are returned alongside (the columns are NOT
        comparable). A vector dim-mismatch (Voyage substrate read offline) is caught and
        surfaced per the documented degradation, leaving the other columns intact.
        """
        decision = self.router.explain(query)
        per_backend = {}
        errors = {}
        for name in READ_ORDER:
            try:
                hits = self.backends[name].search(query, k=k)
                per_backend[name] = [_hit(h) for h in hits]
            except ValueError as exc:  # dim mismatch in _cosine on a Voyage substrate read offline
                per_backend[name] = []
                errors[name] = _dim_hint(name, exc)
            except Exception as exc:   # never let one backend take down the probe view
                per_backend[name] = []
                errors[name] = f"{type(exc).__name__}: {exc}"
        try:
            engine_hits = [_hit(h) for h in self.engine.search(query, k=k)]
            engine_error = None
        except ValueError as exc:
            engine_hits = []
            engine_error = _dim_hint("engine", exc)
        except Exception as exc:
            engine_hits = []
            engine_error = f"{type(exc).__name__}: {exc}"
        return {
            "query": query,
            "k": k,
            "decision": decision,
            "per_backend": per_backend,
            "engine": engine_hits,
            "engine_error": engine_error,
            "errors": errors,
            "score_semantics": dict(SCORE_SEMANTICS),
        }

    def artifact_view(self, item_id: str, backend: str) -> dict:
        """The actual STORED ARTIFACT for one memory in one backend, plus the on-disk
        path to copy. Distinct from a retrieval probe: this shows WHAT IS STORED, not
        what a query retrieves. Powers the Browse backend-badge popover.

        * ``markdown`` → the real OKF ``.md`` file: raw ``text`` + parsed ``frontmatter``
          + ``body``; ``copy_path`` is that PER-MEMORY file
          (``<bundle>/<type-slug>/<id-slug>.md``).
        * ``vectors``  → the stored ``item`` (content/tags/metadata) + ``embedding``
          (dim/model/index); ``copy_path`` is the SHARED ``memory.db`` (vectors are rows,
          not per-memory files — the raw float vector is opaque and omitted).
        * ``graph``    → the ``node`` (content/metadata) + its typed ``edges``;
          ``copy_path`` is the SHARED ``graph.db``.
        """
        if not item_id:
            raise ValueError("item_id is required")
        if backend not in READ_ORDER:
            raise ValueError(f"unknown backend {backend!r}; choose {'|'.join(READ_ORDER)}")

        canon, _ = self._canonical()
        item = canon.get(item_id)
        if item is None:
            raise KeyError(item_id)

        root = Path(self.store_dir)
        base = {
            "item_id": item_id,
            "backend": backend,
            "backend_present": self.backend_status.get(backend) == "ok",
            "score_semantics": SCORE_SEMANTICS[backend],
        }
        if backend == MARKDOWN:
            base.update(self._md_artifact(item, root))
        elif backend == VECTORS:
            base.update(self._vec_artifact(item_id, root))
        else:
            base.update(self._graph_artifact(item_id, item, root))
        return base

    def _md_artifact(self, item: MemoryItem, root: Path) -> dict:
        """The actual OKF ``.md`` file for ``item`` — the documented relpath under the
        live bundle root, with an ``rglob`` fallback if a type-change moved it. Reads the
        file verbatim (the one place the inspector shows on-disk file text, by design —
        this view's whole purpose is "see the actual file")."""
        store = self.backends[MARKDOWN]
        bundle = getattr(getattr(store, "_okf", None), "root", None) or (root / _ARTIFACT[MARKDOWN])
        bundle = Path(bundle)
        rel = _doc_relpath(item)
        path = bundle / rel
        if not path.exists() and bundle.is_dir():
            matches = sorted(bundle.rglob(Path(rel).name))
            if matches:
                path = matches[0]
        out: dict = {"kind": "markdown", "copy_path": str(path), "exists": path.exists()}
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8")
                fm, body = split_doc(text)
                out.update(text=text, frontmatter=fm, body=body)
            except OSError as exc:
                out["error"] = f"could not read file: {exc}"
        else:
            out["note"] = "no .md file on disk for this memory (markdown backend absent or file missing)"
            out["bundle_dir"] = str(bundle)
        return out

    def _vec_artifact(self, item_id: str, root: Path) -> dict:
        """The stored vector-backend record for ``item_id`` + embedding metadata. The raw
        float vector lives as an opaque row in the shared ``memory.db`` (the ``copy_path``);
        we surface dim/model/index, not the numbers."""
        store = self.backends[VECTORS]
        db = root / _ARTIFACT[VECTORS]
        out: dict = {"kind": "vector", "copy_path": str(db), "exists": db.exists()}
        item = store.get(item_id) if self.backend_status.get(VECTORS) == "ok" else None
        if item is not None:
            out["item"] = _artifact_item(item)
        out["embedding"] = {
            "dim": getattr(getattr(store, "_embed", None), "dim", None),
            "model": getattr(store, "embed_model", None),
            "index": getattr(store, "vector_index_status", None),
            "note": "stored as an opaque float[dim] row in memory.db; raw values omitted",
        }
        return out

    def _graph_artifact(self, item_id: str, canonical: MemoryItem, root: Path) -> dict:
        """The graph node for ``item_id`` (content/metadata) + its typed edges. Nodes +
        edges live in the shared ``graph.db`` (the ``copy_path``)."""
        store = self.backends[GRAPH]
        db = root / _ARTIFACT[GRAPH]
        out: dict = {"kind": "graph", "copy_path": str(db), "exists": db.exists()}
        node = store.get(item_id) if self.backend_status.get(GRAPH) == "ok" else None
        src = node if node is not None else canonical
        if src is not None:
            out["node"] = _artifact_item(src)
        out["edges"] = self._edges_for(item_id, canonical)
        return out

    # -- eval-case capture -------------------------------------------------
    def capture(self, payload: dict) -> dict:
        """Append a captured eval case to ``captured_cases.jsonl`` next to this module.

        No reusable EXTERNAL routing-eval fixture format exists in the repo — the
        committed routing/retrieval corpora are Python tuple literals inside the
        ``stores/tests/test_*_evals.py`` files (e.g. ``BLIND_CASES``), not an
        appendable JSON/JSONL file. So per the build spec's fallback we record minimal
        JSONL cases here for a human to fold into a test corpus. A ``route`` case mirrors
        the ``(query/content, expected_backend)`` shape; a ``retrieval`` case records
        ``(query, expected_ids)``.
        """
        kind = (payload.get("kind") or "route").strip()
        if kind not in ("route", "retrieval"):
            raise ValueError("kind must be 'route' or 'retrieval'")
        record = {
            "kind": kind,
            "expected": payload.get("expected") or {},
            "note": payload.get("note") or "",
            "profile": self.profile,
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if kind == "route":
            record["content"] = payload.get("content") or payload.get("query") or ""
        else:
            record["query"] = payload.get("query") or payload.get("content") or ""
        path = captured_cases_path()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        return {"ok": True, "path": str(path), "count": _line_count(path), "record": record}


def captured_cases_path() -> Path:
    """Path to the captured-cases JSONL (next to this module)."""
    return Path(__file__).resolve().parent / "captured_cases.jsonl"


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #
def open_substrate(store_dir: str, profile: Optional[str] = "auto", *,
                   margin_threshold: float = DEFAULT_MARGIN_THRESHOLD) -> Substrate:
    """Open ``store_dir`` read-only and wire a Router/RouterStore over it.

    Mirrors ``build_store``'s profile selection and backend layout. A backend whose
    artifact is absent becomes a read-only :class:`_EmptyStore` (never created on disk).
    The accuracy profile (real Voyage embedder, dim 1024) is built only when its key is
    present; if it can't be constructed the substrate degrades to the offline fusion
    profile with a recorded warning, so browsing always works.
    """
    root = resolve_store_root(store_dir)
    effective, source = resolve_profile(profile)
    warnings: list = []
    if str(root) != str(Path(store_dir)):
        warnings.append(f"backends found under nested plugin store dir: {root}")

    status = {n: ("ok" if (root / _ARTIFACT[n]).exists() else "absent") for n in READ_ORDER}

    embed = None
    if effective == "accuracy":
        embed = _try_voyage(warnings)
        if embed is None:
            effective = "fusion"
            source += " → degraded to fusion (accuracy embedder unavailable)"

    backends = {
        MARKDOWN: _open_markdown(root, status),
        VECTORS: _open_vectors(root, status, embed),
        GRAPH: _open_graph(root, status, embed),
    }

    config = _build_config(effective, embed, warnings)
    router = Router.with_config(backends, config)
    engine = RouterStore(router)
    return Substrate(
        store_dir=str(root), backends=backends, router=router, engine=engine,
        profile=effective, profile_source=source, backend_status=status,
        warnings=warnings, margin_threshold=margin_threshold,
    )


def _build_config(effective: str, embed, warnings: list):
    if effective == "accuracy" and embed is not None:
        from memeval.router import SemanticRouterClassifier
        return accuracy_profile(
            classifier=SemanticRouterClassifier(embed),
            embed=embed,
            embed_model=getattr(embed, "model", None),
        )
    if effective == "fusion":
        return fusion_profile()
    return speed_profile()


def _try_voyage(warnings: list):
    """Construct a Voyage embedder for the accuracy profile, or record why it's absent."""
    try:
        from memeval.stores.embedders import VoyageEmbedder
        return VoyageEmbedder()
    except Exception as exc:
        warnings.append(
            f"accuracy profile unavailable ({type(exc).__name__}: {exc}); "
            "set VOYAGE_API_KEY and install the embedder, or use --profile fusion. "
            "Falling back to offline fusion."
        )
        return None


def _open_markdown(root: Path, status: dict):
    if status[MARKDOWN] != "ok":
        return _EmptyStore()
    return MarkdownStore(root / _ARTIFACT[MARKDOWN])


def _open_vectors(root: Path, status: dict, embed):
    if status[VECTORS] != "ok":
        return _EmptyStore()  # do NOT create memory.db on an empty substrate
    db = str(root / _ARTIFACT[VECTORS])
    if embed is not None:
        return SqliteVectorStore(db, embed=embed, embed_model=getattr(embed, "model", None),
                                 dim=getattr(embed, "dim", 1024))
    return SqliteVectorStore(db)


def _open_graph(root: Path, status: dict, embed):
    if status[GRAPH] != "ok":
        return _EmptyStore()  # do NOT create graph.db on an empty substrate
    return GraphStore(path=str(root / _ARTIFACT[GRAPH]), embed=embed)


# --------------------------------------------------------------------------- #
# Small helpers (all over PUBLIC data only)
# --------------------------------------------------------------------------- #
def _split_link(entry: Any) -> tuple:
    """Normalize one ``okf_links`` entry to ``(anchor, raw_target)`` across the typed
    forms (``(anchor, target)`` / ``[anchor, target]`` / ``{"rel"/"relation", "target"}``)
    and the legacy bare-target string (anchor = ``""`` → ``relates_to``)."""
    if isinstance(entry, str):
        return ("", entry)
    if isinstance(entry, dict):
        anchor = entry.get("rel") or entry.get("relation") or entry.get("anchor") or ""
        target = entry.get("target") or entry.get("to") or ""
        return (anchor, target)
    if isinstance(entry, (list, tuple)) and len(entry) == 2:
        return (entry[0] or "", entry[1])
    return ("", str(entry))


def _target_id(target: Any) -> str:
    """Last path segment of an OKF link target, minus ``.md`` (mirrors the graph store)."""
    tail = str(target).rstrip("/").rsplit("/", 1)[-1]
    return tail[:-3] if tail.endswith(".md") else tail


def _snippet(content: str, n: int = 140) -> str:
    s = " ".join((content or "").split())
    return (s[: n - 1] + "…") if len(s) > n else s


def _hit(h) -> dict:
    return {
        "item_id": h.item_id,
        "score": h.score,
        "rank": h.rank,
        "snippet": _snippet(h.item.content),
        "tags": list(h.item.tags),
    }


def _artifact_item(item: MemoryItem) -> dict:
    """Public fields of a stored MemoryItem for an artifact-view popover."""
    return {
        "content": item.content,
        "tags": list(item.tags),
        "metadata": dict(item.metadata or {}),
        "timestamp": item.timestamp,
        "version": item.version,
    }


def _dim_hint(name: str, exc: Exception) -> str:
    return (f"vector probe unavailable — embedding dim mismatch ({exc}). "
            "This substrate was written with a real embedder (e.g. Voyage, dim 1024); "
            "set VOYAGE_API_KEY and reopen with --profile accuracy. "
            "Browse and routing are unaffected.")


def _line_count(path: Path) -> int:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


__all__ = ["Substrate", "open_substrate", "resolve_profile", "captured_cases_path",
           "READ_ORDER", "SCORE_SEMANTICS", "DEFAULT_MARGIN_THRESHOLD"]
