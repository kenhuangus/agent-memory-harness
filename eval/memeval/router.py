"""Intelligent router — owner: Brent (@bgibson1618). Query dispatch over the stores.

Classifies a memory query and routes it to the SINGLE best backend instead of
fanning out: relationship/contradiction queries -> graph, conceptual/"why"
queries -> vectors, literal keyword/identifier lookups -> markdown. Rule-based and
deterministic (v1); a learned upgrade (a fine-tuned local model) slots behind the
same `route()` signature later (a learned classifier can swap in behind it).

Division of labor: the PRIMARY AGENT decides *if* to retrieve;
the router owns *where & how*. So cascade/fall-through across backends is the
router's concern, not the caller's. v1 is single-route + graceful degradation to an
available backend; a cascade / meta-index can grow here later.

Approach: cheap signal functions contribute to a per-backend score; argmax wins
(ties + no-signal -> the semantic default). The top-two margin is a ready-made
"routing confidence" / fusion-trigger signal (see `explain`). Stdlib only,
deterministic, no network.

D008 / D016 — profile-ready cascade. The classifier is now a swappable seam
(:class:`RuleBasedClassifier` behind :class:`RouterClassifier`), and a frozen
:class:`RouterConfig` selects a routing *profile*. ``RouterConfig()`` reproduces
today's router byte-for-byte (rule classifier, cascade off). When a profile turns
the cascade on, a GRAPH-classified query routes to a retrieval-only
:class:`_GraphVectorCascade` that reproduces the D008 PR1 exact-anchor gate +
``item_id`` projection on the real stores. Stdlib only; no new dependencies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from .protocols import MemoryStore
from .schema import MemoryItem, RetrievedItem

GRAPH = "graph"
VECTORS = "vectors"
MARKDOWN = "markdown"

# Signal weights. Intent predicates are strong; code tokens are modest so a
# semantic "why <code_token>" still routes to vectors, not markdown.
_STRONG = 3.0
_TOKEN = 1.0
_SHORT = 1.5

# Relationship / traversal / dependency / impact intent -> graph.
# Note: "called" (naming) is intentionally NOT here (it's literal); only "call(s|ing)"
# (the call-graph sense) is. "using" was dropped — too broad ("by using X" != depends).
_GRAPH_RE = re.compile(
    r"\bdepends?\s+on\b|\bdepend(?:s|ency|encies)?\b"
    r"|\bcall(?:s|ing)?\b|\buses?\b|\bused\s+by\b|\bimport(?:s|ed|ing)?\b"
    r"|\bconnect(?:s|ed|ion)?\s+to\b|\bconnected\b"
    r"|\brelate[sd]?\b|\brelationship\b"
    r"|\bconflicts?\s+with\b|\bcontradicts?\b"
    r"|\blinked?\s+to\b"
    r"|\brenam(?:e|es|ed|ing)\b|\bimpact(?:s|ed)?\b|\baffect(?:s|ed)?\b|\bwhat\s+breaks?\b",
    re.I,
)
# Note: "compare" and "X between Y" were dropped here — they read
# structural but usually mean "synthesize this for me", so they live in _VECTOR_RE now.

# Conceptual / rationale / synthesis intent -> vectors. Overrides surface code tokens.
_VECTOR_RE = re.compile(
    r"\bwhy\b|\bhow\s+come\b|\breason(?:ing|s|ed)?\b|\brationale\b"
    r"|\bsummar(?:y|ies|ize|ise)\b|\bexplain\b|\boverview\b"
    r"|\btrade[\s-]?offs?\b|\bdecid(?:e|ed|es|ing)\b|\bdecision\b"
    r"|\bchose\b|\bchoose\b|\bchoosing\b|\bthoughts?\s+on\b|\bapproach\b"
    r"|\bcompar(?:e|es|ed|ing)\b|\bcomparisons?\b|\beverything\b.{0,20}\babout\b"
    r"|\banything\b.{0,20}\babout\b|\btell\s+me\s+about\b|\bwhat\s+do\s+we\s+know\b",
    re.I,
)

# Literal-lookup intent -> markdown, even inside a question. "called" = naming
# ("what is X called", "the flag called Y"), a literal/keyword ask, not a call-graph.
_LITERAL_RE = re.compile(
    r"\b(?:exact\s+)?name\s+(?:of|for)\b|\bvalue\s+of\b|\bsignature\b"
    r"|\bdefinition\s+of\b|\bdefined\b|\bspelling\b|\bfile\b|\bcalled\b",
    re.I,
)

# Code-shaped tokens -> markdown (case-sensitive on purpose). Modest weight, capped.
_CODE_RE = re.compile(
    r"`[^`]+`"                                       # backticked span
    r"|[A-Za-z_][\w./-]*\.(?:py|md|json|txt|ya?ml)\b"  # filename.ext
    r"|[a-z]+_[a-z0-9_]+"                             # snake_case
    r"|[a-z]+[A-Z]\w*"                                # camelCase / internal cap
    r"|\b[A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)*\b"          # ALL_CAPS / CONSTANTS
    r"|\b\w+\(\)"                                     # func()
)
_QUOTED_RE = re.compile(r"[\"'][^\"']+[\"']")
_QUESTION_RE = re.compile(
    r"^\s*(?:what|which|where|who|whose|when|how|is|are|do|does|can|could|should)\b", re.I
)

# Tie-break priority: more specific intents beat the semantic default.
_PRIORITY = (GRAPH, MARKDOWN, VECTORS)
# Graceful-degradation order when the chosen backend isn't registered.
_FALLBACK = (VECTORS, MARKDOWN, GRAPH)


def _score(query: str) -> dict[str, float]:
    """Per-backend signal score for ``query`` (higher = stronger fit)."""
    scores = {GRAPH: 0.0, VECTORS: 0.0, MARKDOWN: 0.0}
    if _GRAPH_RE.search(query):
        scores[GRAPH] += _STRONG
    if _VECTOR_RE.search(query):
        scores[VECTORS] += _STRONG
    if _LITERAL_RE.search(query):
        scores[MARKDOWN] += _STRONG
    code_hits = len(_CODE_RE.findall(query)) + len(_QUOTED_RE.findall(query))
    if code_hits:
        scores[MARKDOWN] += min(2.0, float(code_hits)) * _TOKEN
    # a short keyword-ish query with no question/relational framing -> literal recall.
    # Count word-like tokens (not raw split) so empty / whitespace / punctuation-only
    # inputs score nothing here and fall to the semantic default instead of markdown.
    words = re.findall(r"[A-Za-z0-9]+", query)
    if 1 <= len(words) <= 3 and not _QUESTION_RE.search(query) and not _GRAPH_RE.search(query):
        scores[MARKDOWN] += _SHORT
    return scores


# --------------------------------------------------------------------------- #
# Classifier seam (D016) — internal; NOT exported to memeval.protocols.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ClassificationResult:
    """The routing decision plus the signal it came from.

    ``choice`` is the winning backend name; ``scores`` is the per-backend signal
    score; ``margin`` is the top-two gap (a routing-confidence / fusion-trigger
    signal). ``details`` is a free slot for a learned classifier's extras.
    """

    choice: str
    scores: dict[str, float]
    margin: float
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class RouterClassifier(Protocol):
    """A swappable query classifier behind :meth:`Router.classify`.

    Structural (duck-typed): the rule-based v1 and a future learned classifier
    both satisfy it without subclassing. Internal to the router; intentionally
    NOT added to ``memeval.protocols`` (that file is the frozen cross-team
    contract — the classifier seam is a router-local concern).
    """

    name: str

    def classify(self, query: str) -> ClassificationResult:
        """Return the routing decision for ``query``."""
        ...


class RuleBasedClassifier:
    """The v1 classifier: wraps :func:`_score` behind the :class:`RouterClassifier` seam.

    Reproduces today's :meth:`Router.classify` exactly: ``config.default_backend``
    on an all-zero (no-signal) query, ``config.priority`` to break ties, and a
    top-two ``margin`` from the sorted scores.
    """

    name = "rule"

    def __init__(self, config: "RouterConfig") -> None:
        self._config = config

    def classify(self, query: str) -> ClassificationResult:
        scores = _score(query or "")
        ranked = sorted(scores.values(), reverse=True)
        margin = ranked[0] - ranked[1] if len(ranked) > 1 else ranked[0]
        best = max(scores.values())
        choice = self._config.default_backend  # no-signal -> semantic default
        if best > 0.0:
            for name in self._config.priority:  # deterministic tie-break
                if scores[name] == best:
                    choice = name
                    break
        return ClassificationResult(choice=choice, scores=scores, margin=margin)


# --------------------------------------------------------------------------- #
# Profile config (D016) — frozen stdlib dataclasses.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CascadeConfig:
    """Graph→vector cascade knobs. ``enabled=False`` keeps single-route behavior.

    The floors + ``gate`` reproduce the D008 PR1 exact-anchor gate;
    ``hydrate_from_vector`` projects accepted graph ids through the vector store
    (graph fallback when absent). ``graph_backend`` / ``vector_backend`` name the
    two registered backends the cascade wraps. ``gate`` selects the gate strategy;
    only ``"exact_anchor"`` ships in PR2 (the cascade fails loud on any other value).
    """

    enabled: bool = False
    graph_backend: str = GRAPH
    vector_backend: str = VECTORS
    score_floor: float = 0.10
    margin_floor: float = 0.05
    gate: str = "exact_anchor"
    hydrate_from_vector: bool = True


@dataclass(frozen=True)
class Consult2Config:
    """Second-opinion / RRF-fusion knobs. Declared for D016; UNUSED in PR2.

    No RRF implementation ships here — ``enabled`` stays ``False`` and the fields
    only reserve the shape a later PR (2.5/3) fills in.
    """

    enabled: bool = False
    margin_below: float = 0.0
    rrf_k: int = 60


@dataclass(frozen=True)
class RouterConfig:
    """A routing *profile*. ``RouterConfig()`` == today's router exactly.

    Defaults mean: rule classifier (``classifier=None`` -> built internally),
    cascade off, consult-2 off, no injected embedder. A profile flips
    ``cascade.enabled`` (and later ``consult2``) on without changing any public
    :class:`Router` signature.
    """

    profile_name: str = "speed"
    classifier: Optional[RouterClassifier] = None
    priority: tuple = _PRIORITY
    fallback: tuple = _FALLBACK
    default_backend: str = VECTORS
    k: int = 5
    cascade: CascadeConfig = field(default_factory=CascadeConfig)
    consult2: Consult2Config = field(default_factory=Consult2Config)
    embed: Optional[Any] = None
    embed_model: Optional[str] = None


# --------------------------------------------------------------------------- #
# Named profile presets (D008 PR2.5) — preset factories over RouterConfig.
#
# Two presets ship as PUBLIC, intent-named entry points. ``balanced`` is
# intentionally NOT a public factory (D016 ruling): it is a profile-matrix
# reporter ROW only — cascade-on over the stdlib stores — promoted to a named
# preset later only if the eval data justifies it.
# --------------------------------------------------------------------------- #
def speed_profile() -> RouterConfig:
    """The default ``speed`` profile: today's router, byte-for-byte.

    Rule-based classifier (built internally), offline hashing embedder, cascade
    OFF — one best-route per query, no fan-out. Identical to ``RouterConfig()``;
    named so a caller can select it by intent rather than by the bare default.
    """
    return RouterConfig()


def accuracy_profile(*, classifier: RouterClassifier, embed: Any,
                     embed_model: Optional[str] = None, k: int = 8) -> RouterConfig:
    """The ``accuracy`` profile: injected classifier + real embedder, cascade ON.

    The heavy strategies are CALLER-INJECTED (PR3): ``classifier`` is a
    :class:`RouterClassifier` (e.g. a learned / spaCy classifier) and ``embed`` is
    a real embedder the vector store is built around. This factory only *builds the
    config* — it turns the graph→vector cascade on and leaves ``consult2`` at its
    declared default (no RRF / second-opinion implementation ships here). ``k`` is
    the profile's retrieval breadth (wider than speed's default 5).
    """
    return RouterConfig(
        profile_name="accuracy",
        classifier=classifier,
        cascade=CascadeConfig(enabled=True),
        embed=embed,
        embed_model=embed_model,
        k=k,
    )


# --------------------------------------------------------------------------- #
# Exact-anchor gate identity helpers (ported from D008 PR1 test_d008_evals).
# --------------------------------------------------------------------------- #
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_ANCHOR_QUOTED_RE = re.compile(r"[\"']([^\"']+)[\"']")

ACCEPT = "accept_graph"
FALLTHROUGH = "fallthrough_vector"

# The only gate strategy implemented in PR2. A profile naming any other gate is a
# configuration error (fail loud, not silently ignored — see _GraphVectorCascade).
_SUPPORTED_GATE = "exact_anchor"


def _norm_identity(text: Any) -> str:
    """Collapse a string to its bare identifier (lowercase, no separators).

    So ``payment-service``, ``PaymentService``, ``payment_service`` and the basename
    of ``memeval://memory/payment-service`` all compare equal — matching a code
    identifier named in a query to the memory item that owns it, while staying an
    EXACT match (no fuzzy / substring matching).
    """
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def _anchor_spans(query: str) -> list[str]:
    """Explicit anchor spans the user named: quoted or backticked substrings."""
    spans = _BACKTICK_RE.findall(query or "") + _ANCHOR_QUOTED_RE.findall(query or "")
    return [s.strip() for s in spans if s.strip()]


def _identity_index(items_by_id: dict[str, MemoryItem]) -> dict[str, set[str]]:
    """Map each item's identity strings (item_id / okf_title / resource basename) -> {item_id}."""
    index: dict[str, set[str]] = {}
    for item_id, item in items_by_id.items():
        meta = item.metadata or {}
        keys = [item_id]
        if meta.get("okf_title"):
            keys.append(meta["okf_title"])
        if meta.get("okf_resource"):
            keys.append(str(meta["okf_resource"]).rstrip("/").rsplit("/", 1)[-1])
        for key in keys:
            norm = _norm_identity(key)
            if norm:
                index.setdefault(norm, set()).add(item_id)
    return index


@dataclass(frozen=True)
class GateResult:
    """The exact-anchor gate's verdict for a GRAPH-routed query."""

    decision: str               # ACCEPT | FALLTHROUGH
    reason: str
    anchored_id: Optional[str]


# --------------------------------------------------------------------------- #
# The production cascade (D008 PR2) — a retrieval-only MemoryStore view.
# --------------------------------------------------------------------------- #
class _GraphVectorCascade:
    """Graph→vector cascade as a retrieval-only :class:`MemoryStore`.

    :meth:`search` runs the graph stage, applies the exact-anchor gate (PR1
    semantics, parameterized by the cascade floors), and on ACCEPT projects the
    accepted graph hits through the vector store (graph fallback) — exact seed
    first, linked neighbors after, with graph-derived ranks. On a fall-through it
    defers to ``vector.search``. ``as_of`` flows into BOTH stages and bounds anchor
    resolution, so no future item leaks into results or the gate verdict.

    This is a *view* over two MUTABLE backends; it does not own storage. :meth:`write`
    therefore raises — write to the underlying graph / vector backends directly. The
    exact-anchor index is rebuilt from the graph's current contents on every
    gate/search (never cached across calls), so an item written to the underlying
    stores after this view is first used is still visible to the gate.
    """

    def __init__(self, graph: MemoryStore, vector: MemoryStore,
                 cascade_config: CascadeConfig) -> None:
        if cascade_config.gate != _SUPPORTED_GATE:
            # PR2 implements only the exact-anchor gate. Fail loud rather than
            # silently ignore an unsupported gate name (a fusion / learned gate is
            # a later PR); a typo'd profile must not quietly route as exact-anchor.
            raise ValueError(
                f"_GraphVectorCascade supports only gate={_SUPPORTED_GATE!r} in PR2; "
                f"got gate={cascade_config.gate!r}"
            )
        self._graph = graph
        self._vector = vector
        self._cfg = cascade_config

    # -- MemoryStore protocol ----------------------------------------------
    def search(self, query: str, *, k: int = 5, as_of: Optional[float] = None,
               **kwargs: Any) -> list[RetrievedItem]:
        """Graph→vector cascade retrieval for ``query`` (gate + projection)."""
        graph_hits = self._graph.search(query, k=k, as_of=as_of)
        gate = self._gate(query, graph_hits, as_of)
        if gate.decision == ACCEPT:
            return self._project(graph_hits)
        return self._vector.search(query, k=k, as_of=as_of)

    def get(self, item_id: str) -> Optional[MemoryItem]:
        """Prefer the vector store's copy, then the graph's."""
        item = self._vector.get(item_id)
        if item is None:
            item = self._graph.get(item_id)
        return item

    def all(self) -> list[MemoryItem]:
        """Union of both backends' items, deduped by ``item_id`` (vector first)."""
        seen: dict[str, MemoryItem] = {}
        for store in (self._vector, self._graph):
            for item in store.all():
                seen.setdefault(item.item_id, item)
        return list(seen.values())

    def write(self, item: MemoryItem) -> None:
        raise NotImplementedError(
            "_GraphVectorCascade is the retrieval-only view returned by Router.route(); "
            "write to the underlying graph/vector backends directly."
        )

    # -- gate + projection -------------------------------------------------
    def gate(self, query: str, *, k: int = 5, as_of: Optional[float] = None) -> GateResult:
        """Introspect the accept/fall-through verdict without retrieving (testable seam)."""
        return self._gate(query, self._graph.search(query, k=k, as_of=as_of), as_of)

    def _gate(self, query: str, graph_hits: list[RetrievedItem],
              as_of: Optional[float]) -> GateResult:
        """Accept only a UNIQUE exact anchor that is graph rank-0 and clears the floors."""
        if not graph_hits:
            return GateResult(FALLTHROUGH, "empty_graph_stage", None)
        anchored_id, reason = self._resolve_anchor(query, as_of)
        if anchored_id is None:
            return GateResult(FALLTHROUGH, reason, None)
        if graph_hits[0].item_id != anchored_id:
            # the explicitly-named anchor is NOT the graph's top hit — refuse
            # (the silent-wrong-success guard).
            return GateResult(FALLTHROUGH, "rank0_not_anchor", anchored_id)
        score0 = graph_hits[0].score
        margin = score0 - graph_hits[1].score if len(graph_hits) > 1 else score0
        if score0 < self._cfg.score_floor or margin < self._cfg.margin_floor:
            return GateResult(FALLTHROUGH, "below_floor", anchored_id)
        return GateResult(ACCEPT, "exact_anchor", anchored_id)

    def _resolve_anchor(self, query: str,
                        as_of: Optional[float]) -> tuple[Optional[str], str]:
        """Resolve quoted/backticked spans to a UNIQUE non-future item id.

        ``as_of`` bounds resolution: an anchor naming an item newer than ``as_of``
        is invisible, so it can neither be accepted nor surfaced in the verdict.
        """
        spans = _anchor_spans(query)
        if not spans:
            return None, "no_explicit_anchor"
        index = self._anchor_index()
        resolved: set[str] = set()
        for span in spans:
            resolved |= index.get(_norm_identity(span), set())
        if as_of is not None:
            resolved = {iid for iid in resolved if not self._is_future(iid, as_of)}
        if len(resolved) != 1:
            reason = "ambiguous_anchor" if len(resolved) > 1 else "no_unique_anchor"
            return None, reason
        return next(iter(resolved)), "exact_anchor"

    def _project(self, graph_hits: list[RetrievedItem]) -> list[RetrievedItem]:
        """Hydrate accepted graph hits via the vector store (graph fallback).

        Preserves graph rank order (exact seed first, linked neighbors after) and
        the graph score; re-ranks 0..n on the hydrated items.
        """
        projected: list[RetrievedItem] = []
        for rank, hit in enumerate(graph_hits):
            item = self._vector.get(hit.item_id) if self._cfg.hydrate_from_vector else None
            if item is None:
                item = self._graph.get(hit.item_id)
            if item is not None:
                projected.append(RetrievedItem(item=item, score=hit.score, rank=rank))
        return projected

    # -- helpers -----------------------------------------------------------
    def _anchor_index(self) -> dict[str, set[str]]:
        """Build the exact-anchor identity index from the graph's CURRENT contents.

        Rebuilt per gate/search call, never cached across calls: the cascade is a
        view over mutable stores (``write`` raises here, so callers add anchors to
        the underlying graph/vector backends directly), and a cached index would make
        any item written after first use invisible to the gate. O(n) per graph-routed
        query is fine and consistent with the offline scan-all design
        (``SqliteVectorStore.search`` already scans every row). Future optimization: a
        revision-keyed cache if a store ever exposes a revision/version counter.
        """
        return _identity_index({it.item_id: it for it in self._graph.all()})

    def _is_future(self, item_id: str, as_of: float) -> bool:
        item = self._graph.get(item_id)
        if item is None:
            item = self._vector.get(item_id)
        return item is not None and item.timestamp > as_of


class Router:
    """Routes a query to one registered :class:`MemoryStore` backend (rule-based v1).

    ``backends`` maps a name (``"graph"`` / ``"vectors"`` / ``"markdown"``) to a
    concrete store. :meth:`classify` is the pure routing decision (testable without
    backends); :meth:`route` resolves it to a registered store, degrading gracefully
    when the chosen backend is absent.

    A :class:`RouterConfig` profile (attached via :meth:`with_config`) can turn the
    graph→vector cascade on: a GRAPH-classified query then routes to a fresh
    :class:`_GraphVectorCascade` built over the currently registered backends (not
    cached — so swapping ``backends`` rebinds the cascade). ``Router()`` (default
    config) is byte-for-byte the v1 router — rule classifier, cascade off.
    """

    def __init__(self, backends: Optional[dict[str, MemoryStore]] = None) -> None:
        self._setup(backends, RouterConfig())

    @classmethod
    def with_config(cls, backends: Optional[dict[str, MemoryStore]] = None,
                    config: Optional[RouterConfig] = None) -> "Router":
        """Build a Router bound to a routing ``config`` profile (cascade-ready)."""
        router = cls.__new__(cls)
        router._setup(backends, config or RouterConfig())
        return router

    def _setup(self, backends: Optional[dict[str, MemoryStore]],
               config: RouterConfig) -> None:
        self.backends = backends or {}
        self._config = config
        self._classifier: RouterClassifier = (
            config.classifier if config.classifier is not None
            else RuleBasedClassifier(config)
        )

    def classify(self, query: str) -> str:
        """Return the best-fit backend name for ``query`` (no backends required)."""
        return self._classifier.classify(query or "").choice

    def explain(self, query: str) -> dict[str, Any]:
        """Decision + scores + top-two margin (a routing-confidence / fusion signal)."""
        result = self._classifier.classify(query or "")
        return {"choice": result.choice, "scores": result.scores, "margin": result.margin}

    def route(self, query: str, **kwargs: Any) -> MemoryStore:
        """Return the store for ``query``, degrading to an available backend.

        When the active profile enables the cascade and a GRAPH-classified query has
        both cascade backends registered, returns a fresh :class:`_GraphVectorCascade`
        bound to the currently registered backends; otherwise the v1
        ``(choice, *fallback)`` resolution.
        """
        choice = self.classify(query)
        cascade = self._config.cascade
        if (cascade.enabled and choice == GRAPH
                and cascade.graph_backend in self.backends
                and cascade.vector_backend in self.backends):
            return self._graph_vector_cascade()
        for name in (choice, *self._config.fallback):
            store = self.backends.get(name)
            if store is not None:
                return store
        raise RuntimeError("Router has no registered backends to route to")

    def _graph_vector_cascade(self) -> _GraphVectorCascade:
        """Construct a fresh cascade over the CURRENTLY registered backends.

        Not memoized: ``self.backends`` may be replaced after the Router is built, and
        each qualifying route must bind to whatever graph/vector stores are registered
        NOW — a cached cascade would keep searching stale backend objects. Construction
        is cheap (the anchor index is built lazily per gate/search), so rebuilding per
        call is fine.
        """
        cascade = self._config.cascade
        return _GraphVectorCascade(
            self.backends[cascade.graph_backend],
            self.backends[cascade.vector_backend],
            cascade,
        )


__all__ = [
    "Router", "RouterConfig", "CascadeConfig", "Consult2Config",
    "speed_profile", "accuracy_profile",
]
