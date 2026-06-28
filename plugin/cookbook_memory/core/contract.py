"""The single import edge between the plugin and the memory engine it builds on.

The plugin reaches the frozen data model (``MemoryItem``, ``RetrievedItem``), the
``MemoryStore`` protocol, and the **fully-assembled** memory engine through this module
alone. Every other plugin module imports these names from here, never from the engine's
source package directly -- so the plugin depends on the engine through exactly one file,
and the source package is swappable by editing only this file (ADR-eval-001).

The plugin is a **dumb client of the engine**: it asks :func:`build_store` for one
opaque :class:`~memeval.protocols.MemoryStore` and calls ``search`` / ``write`` on it.
It does NOT know that profiles, classifiers, embedders, cascades, fusion, rerankers, or
write-routing exist -- ALL of that assembly lives here, behind the seam (the engine owns
*how* to retrieve and *where* to store; the plugin owns neither). Selecting and wiring a
routing profile is the engine's job; the plugin specifies none of it.

The engine imports are lazy (resolved on first use inside :func:`build_store`) so the
plugin imports cleanly when the engine isn't installed; the data-model and protocol names
are needed at import time and are imported eagerly.
"""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from typing import Any, Optional

from memeval.protocols import MemoryStore
from memeval.schema import MemoryItem, RetrievedItem


def build_store(store_path: str) -> MemoryStore:
    """Assemble the fully-configured memory engine over ``store_path`` and return it opaquely.

    This is the seam's whole job: build every backend, pick a routing **profile**, wire the
    matching classifier / embedder / cascade / fusion, and hand back ONE object satisfying the
    :class:`~memeval.protocols.MemoryStore` five-method protocol (a :class:`RouterStore` over a
    configured :class:`Router`). The plugin treats the result as a black box -- it never sees a
    profile name, an embedder, or a backend. Both paths are live through the one object: a routed
    read (``search`` dispatches per query, transparently using cascade/fusion when the profile
    enables them) and a routed write (``write`` runs dedup + write-routing across the policy
    backends).

    **Profile selection** is the engine's call, not the plugin's, and needs no plugin input:

    * ``$MEMORY_PROFILE`` (``speed`` | ``fusion`` | ``fusion-local`` | ``fusion-falkor`` |
      ``accuracy`` | ``accuracy-local``) forces a profile when set.
    * Otherwise: if a real embedder key (``$VOYAGE_API_KEY``) is present, use the **accuracy**
      profile (semantic-exemplar classifier + Voyage embedder wired into the vector store at the
      matching dimension + graph->vector cascade). With no key, use the **fusion** profile
      (cross-backend RRF -- the best fully-offline recall; no key, no embedder-dimension mismatch).
      ``speed`` (the bare v1 router) is never auto-selected -- it is reachable only by explicit
      ``$MEMORY_PROFILE=speed``.
    * ``fusion-falkor`` is the **fusion** profile with ONLY its graph backend swapped for an
      EXTERNAL FalkorDB server: it CONNECTS to ``$FALKORDB_URL`` (default ``redis://localhost:6379``)
      with ``native=True``; every other backend (vectors, markdown, fts5) is byte-identical to
      ``fusion``. Reachable ONLY by explicit ``$MEMORY_PROFILE=fusion-falkor`` -- never auto-selected.
      It fails **loud** if the server is unreachable; it does NOT fall back to the in-memory graph,
      because a silent fallback would mislabel the graph backend and corrupt a ``fusion`` vs
      ``fusion-falkor`` measurement (contrast ``fusion-local``/``accuracy-local``, whose fallback is a
      still-valid offline profile). Requires a running (e.g. Docker) FalkorDB at ``$FALKORDB_URL``.
    * ``$MEMEVAL_LOCAL_ANN=1`` opts into ``accuracy-local`` when ``$MEMORY_PROFILE`` is not set:
      local MiniLM embeddings plus sqlite-vec when available, with exact brute-force fallback.

    **Recall score floor** (``$RECALL_MIN_SCORE``) — a precision knob over the FINAL recall hits: any hit
    scoring below the floor is dropped, so a weak all-garbage recall can return fewer than ``k`` hits, or
    nothing, instead of ``k`` weak matches. When ``$RECALL_MIN_SCORE`` is set it overrides for ANY profile
    (a value ``<= 0`` disables the floor). When it is UNSET, only the ``accuracy`` profile gets a default:
    ``0.15`` — the accuracy-calibrated value from n=52 real accuracy/Voyage recalls (PROVISIONAL; a clean
    bimodal split put garbage recalls' top score ``<= 0.09`` and real matches ``>= 0.189``, so 0.15 sits in
    the gap). Every OTHER profile defaults to NO floor (only accuracy was measured). Set ``RECALL_MIN_SCORE=0``
    to disable, or a float to override.

    Kept lazy so a missing engine surfaces as a handled construction failure (the caller falls
    back to a fail-open no-op) rather than an import-time crash.
    """
    from memeval.router import (
        Router,
        RouterStore,
        SemanticRouterClassifier,
        accuracy_local_profile,
        accuracy_profile,
        fusion_profile,
        speed_profile,
    )
    from memeval.stores import Fts5Store, GraphStore, MarkdownStore, SqliteVectorStore
    from memeval.stores.embedders import SentenceTransformersEmbedder, VoyageEmbedder
    from memeval.stores.sqlite_store import SQLITE_VEC_ANN_OVERFETCH, SQLITE_VEC_DIM

    root = Path(store_path)
    root.mkdir(parents=True, exist_ok=True)
    db_path = str(root / "memory.db")

    profile = (os.environ.get("MEMORY_PROFILE") or "").strip().lower()
    if not profile:
        if os.environ.get("MEMEVAL_LOCAL_ANN") == "1":
            profile = "accuracy-local"
        else:
            profile = "accuracy" if os.environ.get("VOYAGE_API_KEY") else "fusion"

    # The accuracy profile is the only one that swaps in a real embedder, which changes the
    # vector dimension -- so it (and ONLY it) builds the vector store AROUND that embedder. Every
    # other profile uses the store's offline default embedder. This keeps the query/stored vector
    # dimensions consistent (a mismatch raises in the store's cosine), which is exactly the
    # internal detail the plugin must never have to reason about.
    if profile == "accuracy":
        embed = VoyageEmbedder()
        embed_model = getattr(embed, "model", None)
        vectors: MemoryStore = SqliteVectorStore(db_path, embed=embed, embed_model=embed_model,
                                                 dim=getattr(embed, "dim", 1024))
        config = accuracy_profile(
            classifier=SemanticRouterClassifier(embed),
            embed=embed,
            embed_model=embed_model,
        )
    elif profile == "accuracy-local":
        try:
            embed = SentenceTransformersEmbedder()
            # Probe once so an explicit local profile falls back before any routed write
            # can create mixed-dimension rows when the package/model is unavailable.
            embed.embed("local profile availability probe", input_type="query")
        except RuntimeError:
            vectors = SqliteVectorStore(db_path)
            config = fusion_profile()
        else:
            embed_model = getattr(embed, "model", None)
            vectors = SqliteVectorStore(
                db_path,
                embed=embed,
                embed_model=embed_model,
                dim=SQLITE_VEC_DIM,
                vector_index="sqlite_vec",
                ann_overfetch=SQLITE_VEC_ANN_OVERFETCH,
                exact_rerank=True,
            )
            config = accuracy_local_profile(
                classifier=SemanticRouterClassifier(embed),
                embed=embed,
                embed_model=embed_model,
            )
    elif profile == "fusion-local":
        try:
            embed = SentenceTransformersEmbedder()
            # Probe once so an explicit local profile falls back before any routed write
            # can create mixed-dimension rows when the package/model is unavailable.
            embed.embed("local profile availability probe", input_type="query")
        except RuntimeError:
            vectors = SqliteVectorStore(db_path)
            config = replace(fusion_profile(), profile_name="fusion-local")
        else:
            embed_model = getattr(embed, "model", None)
            vectors = SqliteVectorStore(
                db_path,
                embed=embed,
                embed_model=embed_model,
                dim=SQLITE_VEC_DIM,
                vector_index="sqlite_vec",
                ann_overfetch=SQLITE_VEC_ANN_OVERFETCH,
                exact_rerank=True,
            )
            # fusion+MiniLM is the D046 winner; avoids D021's classifier regression.
            config = replace(
                fusion_profile(),
                profile_name="fusion-local",
                embed=embed,
                embed_model=embed_model,
            )
    elif profile == "fusion-falkor":
        # Routing config IS the fusion profile -- only the graph BACKEND differs (swapped below for an
        # external FalkorDB). profile_name records the swap so a run is labelled fusion-falkor, not fusion.
        vectors = SqliteVectorStore(db_path)
        config = replace(fusion_profile(), profile_name="fusion-falkor")
    else:
        vectors = SqliteVectorStore(db_path)
        config = fusion_profile() if profile == "fusion" else speed_profile()

    # The graph backend. Every profile except fusion-falkor uses the in-memory GraphStore persisted
    # under the store root so the graph (typed OKF links) survives a process exit like the vector and
    # markdown layers do -- without a path, GraphStore is RAM-only and evaporates each turn, contributing
    # nothing to a memory that accumulates across runs. fusion-falkor swaps in the EXTERNAL FalkorDB
    # server instead (fail-loud, no silent fallback -- see _build_falkor_graph).
    if profile == "fusion-falkor":
        graph: MemoryStore = _build_falkor_graph()
    else:
        graph = GraphStore(path=str(root / "graph.db"))

    # Recall score FLOOR (precision over the FINAL hits): $RECALL_MIN_SCORE overrides for ANY profile
    # (<= 0 disables); unset -> only `accuracy` gets the calibrated 0.15 default, every other profile
    # stays floor-free (only accuracy was measured). Resolved from the profile NAME, so `accuracy-local`
    # / `fusion-local` (which can fall back to a fusion config offline) do NOT inherit accuracy's floor.
    config = replace(config, recall_min_score=_resolve_recall_min_score(profile))

    # All other backends persist under the store root and are IDENTICAL to the fusion path.
    backends: dict[str, MemoryStore] = {
        "vectors": vectors,
        "markdown": MarkdownStore(root / "markdown"),
        "graph": graph,
        # FTS5 is the lexical backend fusion fans out to; Track 0 recall@10 beat markdown 0.842 vs 0.825.
        "fts5": Fts5Store(str(root / "fts5.db")),
    }
    store = RouterStore(Router.with_config(backends, config))
    # Observability: stamp the effective profile + score floor on the returned store so a run's retrieval
    # config is never ambiguous on disk. The plugin surfaces these in the recall event meta (client.py);
    # additive attributes, read defensively there (the plugin still treats the store as an opaque box).
    store.profile_name = config.profile_name
    store.recall_min_score = config.recall_min_score
    return _maybe_wrap_coverage(store)


def _maybe_wrap_coverage(store: MemoryStore) -> MemoryStore:
    """Optionally wrap the routed store in deterministic coverage re-ranking (no LLM).

    Re-ranks the inner top-N by ``alpha*similarity + (1-alpha)*key-coverage`` (fraction of the
    query's salient key tokens present in the candidate). Off by default:

    * ``$MEMORY_COVERAGE_RERANK=1`` (or ``on``/``true``) — enable.
    * ``$MEMORY_COVERAGE_ALPHA`` — similarity weight in the blend (default 0.5).
    * ``$MEMORY_COVERAGE_FETCH`` — candidates over-fetched before re-rank (default 30).

    Preserves the observability attrs; lazy import.
    """
    choice = (os.environ.get("MEMORY_COVERAGE_RERANK") or "").strip().lower()
    if choice in ("", "0", "off", "false", "none"):
        return store
    from memeval.stores.coverage_rank import CoverageRerankStore

    try:
        alpha = float(os.environ.get("MEMORY_COVERAGE_ALPHA", "0.5"))
    except ValueError:
        alpha = 0.5
    try:
        fetch = int(os.environ.get("MEMORY_COVERAGE_FETCH", "30"))
    except ValueError:
        fetch = 30
    wrapped = CoverageRerankStore(store, fetch=fetch, alpha=alpha)
    for attr in ("profile_name", "recall_min_score"):
        if hasattr(store, attr):
            setattr(wrapped, attr, getattr(store, attr))
    return wrapped


def _build_falkor_graph() -> MemoryStore:
    """Build the external-FalkorDB graph backend for ``fusion-falkor`` -- fail-loud, no fallback.

    Connects to ``$FALKORDB_URL`` (default ``redis://localhost:6379``) with ``native=True`` and the
    same default traversal depth the in-memory :class:`GraphStore` uses, so only the storage layer
    differs from ``fusion``. ``FalkorGraphStore``'s constructor round-trips to the server (index +
    max-seq read), so *constructing it IS the reachability probe*. Any failure is re-raised LOUD:
    ``fusion-falkor`` must NEVER silently fall back to the in-memory ``GraphStore``, because that
    would mislabel the graph backend and corrupt a ``fusion`` vs ``fusion-falkor`` measurement.

    Kept lazy (imports happen only here) so the offline default never imports ``falkordb``.
    """
    # Lazy: keep falkordb/redis off the offline import path -- only loaded when fusion-falkor is chosen.
    from memeval.stores.falkor_store import FalkorGraphStore
    from memeval.stores.graph_store import _MAX_DEPTH

    url = (os.environ.get("FALKORDB_URL") or "redis://localhost:6379").strip() or "redis://localhost:6379"
    try:
        return FalkorGraphStore(url=url, native=True, max_depth=_MAX_DEPTH)
    except Exception as exc:
        raise RuntimeError(
            f"MEMORY_PROFILE=fusion-falkor requires a reachable FalkorDB at $FALKORDB_URL ({url!r}), "
            "but building the graph backend failed. Start a Docker FalkorDB (e.g. "
            "`docker run -p 6379:6379 falkordb/falkordb`) or point $FALKORDB_URL at one. fusion-falkor "
            "does NOT fall back to the in-memory GraphStore -- a silent fallback would mislabel the "
            "graph backend and corrupt a fusion vs fusion-falkor measurement."
        ) from exc


def _resolve_recall_min_score(profile: str) -> Optional[float]:
    """The effective recall score floor for ``profile`` (see :func:`build_store` for the full contract).

    ``$RECALL_MIN_SCORE`` set and parseable wins for ANY profile: a value ``> 0`` is the floor; ``<= 0``
    disables it (``None``). An unparseable value is ignored (falls through to the profile default), so a
    typo never silently turns the floor off. With the env unset/blank, only the ``accuracy`` profile
    carries a default floor (``0.15``, the calibrated value); every other profile defaults to ``None``.
    """
    raw = os.environ.get("RECALL_MIN_SCORE")
    if raw is not None and raw.strip() != "":
        try:
            val = float(raw)
        except ValueError:
            pass  # unparseable -> fall through to the profile default (don't silently disable)
        else:
            return val if val > 0 else None  # <= 0 explicitly disables the floor
    return 0.15 if profile == "accuracy" else None


__all__ = ["MemoryItem", "RetrievedItem", "MemoryStore", "build_store"]
