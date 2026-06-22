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

import os
from pathlib import Path
from typing import Any, Optional

from memeval.protocols import MemoryStore
from memeval.schema import MemoryItem, RetrievedItem


def build_store(store_path: str) -> MemoryStore:
    """Assemble the fully-configured memory engine over ``store_path`` and return it opaquely.

    This is the seam's whole job: build every backend, pick a routing **profile**, wire the
    matching classifier / embedder / cascade / fusion, and hand back ONE object satisfying the
    :class:`~memeval.protocols.MemoryStore` four-method protocol (a :class:`RouterStore` over a
    configured :class:`Router`). The plugin treats the result as a black box -- it never sees a
    profile name, an embedder, or a backend. Both paths are live through the one object: a routed
    read (``search`` dispatches per query, transparently using cascade/fusion when the profile
    enables them) and a routed write (``write`` runs dedup + write-routing across the policy
    backends).

    **Profile selection** is the engine's call, not the plugin's, and needs no plugin input:

    * ``$MEMORY_PROFILE`` (``speed`` | ``fusion`` | ``accuracy``) forces a profile when set.
    * Otherwise: if a real embedder key (``$VOYAGE_API_KEY``) is present, use the **accuracy**
      profile (semantic-exemplar classifier + Voyage embedder wired into the vector store at the
      matching dimension + graph->vector cascade). With no key, use the **fusion** profile
      (cross-backend RRF -- the best fully-offline recall; no key, no embedder-dimension mismatch).
      ``speed`` (the bare v1 router) is never auto-selected -- it is reachable only by explicit
      ``$MEMORY_PROFILE=speed``.

    Kept lazy so a missing engine surfaces as a handled construction failure (the caller falls
    back to a fail-open no-op) rather than an import-time crash.
    """
    from memeval.router import (
        Router,
        RouterStore,
        SemanticRouterClassifier,
        accuracy_profile,
        fusion_profile,
        speed_profile,
    )
    from memeval.stores import GraphStore, MarkdownStore, SqliteVectorStore
    from memeval.stores.embedders import VoyageEmbedder

    root = Path(store_path)
    root.mkdir(parents=True, exist_ok=True)
    db_path = str(root / "memory.db")

    profile = (os.environ.get("MEMORY_PROFILE") or "").strip().lower()
    if not profile:
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
    else:
        vectors = SqliteVectorStore(db_path)
        config = fusion_profile() if profile == "fusion" else speed_profile()

    backends: dict[str, MemoryStore] = {
        "vectors": vectors,
        "markdown": MarkdownStore(root / "markdown"),
        "graph": GraphStore(),
    }
    return RouterStore(Router.with_config(backends, config))


__all__ = ["MemoryItem", "RetrievedItem", "MemoryStore", "build_store"]
