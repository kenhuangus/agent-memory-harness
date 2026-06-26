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

import inspect
import math
import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from .protocols import MemoryStore
from .schema import MemoryItem, RetrievedItem

GRAPH = "graph"
VECTORS = "vectors"
MARKDOWN = "markdown"
FTS5 = "fts5"

# Signal weights. Intent predicates are strong; code tokens are modest so a
# semantic "why <code_token>" still routes to vectors, not markdown.
_STRONG = 3.0
_TOKEN = 1.0
_SHORT = 1.5

# Relationship / traversal / dependency / impact intent -> graph.
# Note: "called"/"calling" (naming) are intentionally NOT here (they're literal); only the
# third-person "call(s)" (the call-graph sense) is. "using" was dropped — too broad
# ("by using X" != depends). "touch(es)" (what else touches X) and "downstream" (impact /
# fan-out) are dependency/impact signals alongside the existing "what breaks"/"affect".
_GRAPH_RE = re.compile(
    r"\bdepends?\s+on\b|\bdepend(?:s|ency|encies)?\b"
    r"|\bcalls?\b|\buses?\b|\bused\s+by\b|\bimport(?:s|ed|ing)?\b"
    r"|\bconnect(?:s|ed|ion)?\s+to\b|\bconnected\b"
    r"|\brelate[sd]?\b|\brelationship\b|\btouch(?:es|ed|ing)?\b"
    r"|\bconflicts?\s+with\b|\bcontradicts?\b"
    r"|\blinked?\s+to\b|\bdownstream\b"
    r"|\brenam(?:e|es|ed|ing)\b|\bimpact(?:s|ed)?\b|\baffect(?:s|ed)?\b|\bwhat\s+breaks?\b",
    re.I,
)
# Note: "compare" and "X between Y" were dropped here — they read
# structural but usually mean "synthesize this for me", so they live in _VECTOR_RE now.

# Conceptual / rationale / synthesis intent -> vectors. Overrides surface code tokens.
# "gist" ("give me the gist of X" / "the gist of what X is for") is a summary ask.
_VECTOR_RE = re.compile(
    r"\bwhy\b|\bhow\s+come\b|\breason(?:ing|s|ed)?\b|\brationale\b"
    r"|\bsummar(?:y|ies|ize|ise)\b|\bexplain\b|\boverview\b|\bgist\b"
    r"|\btrade[\s-]?offs?\b|\bdecid(?:e|ed|es|ing)\b|\bdecision\b"
    r"|\bchose\b|\bchoose\b|\bchoosing\b|\bthoughts?\s+on\b|\bapproach\b"
    r"|\bcompar(?:e|es|ed|ing)\b|\bcomparisons?\b|\beverything\b.{0,20}\babout\b"
    r"|\banything\b.{0,20}\babout\b|\btell\s+me\s+about\b|\bwhat\s+do\s+we\s+know\b",
    re.I,
)

# Literal-lookup intent -> markdown, even inside a question. "called"/"calling" = naming
# ("what is X called", "what we ended up calling X"), a literal/keyword ask, not a
# call-graph. "value of" was dropped — "the value of doing X" is benefit/rationale, not a
# literal field value; a real value lookup still routes here via its code token.
_LITERAL_RE = re.compile(
    r"\b(?:exact\s+)?name\s+(?:of|for)\b|\bsignature\b"
    r"|\bdefinition\s+of\b|\bdefined\b|\bspelling\b|\bfile\b|\bcalled\b|\bcalling\b",
    re.I,
)

# Code-shaped tokens -> markdown (case-sensitive on purpose). Modest weight, capped.
# URL / rooted-path / env-var literals are markdown signals too: a user pasting a URL,
# a "/a/b/c" path, or an "ANTHROPIC_"-shaped env-var name wants the literal string.
_CODE_RE = re.compile(
    r"`[^`]+`"                                       # backticked span
    r"|https?://\S+"                                  # URL literal
    r"|/\w[\w.#-]*(?:/[\w.#-]+)+"                      # rooted multi-segment path /a/b
    r"|[A-Za-z_][\w./-]*\.(?:py|md|json|txt|ya?ml)\b"  # filename.ext
    r"|[a-z]+_[a-z0-9_]+"                             # snake_case
    r"|[a-z]+[A-Z]\w*"                                # camelCase / internal cap
    r"|\b[A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)*\b"          # ALL_CAPS / CONSTANTS
    r"|\b[A-Z][A-Z0-9]*_[A-Z0-9_]*"                   # env-var-shaped, incl. trailing _ (ANTHROPIC_)
    r"|\b\w+\(\)"                                     # func()
)
_QUOTED_RE = re.compile(r"[\"'][^\"']+[\"']")
# Backticked / quoted spans are literals the user wants found verbatim — a graph/vector/
# literal trigger word INSIDE one must not fire intent (it still counts as a markdown
# code/quote token). Intent regexes match the literal-stripped text; tokens, the full query.
_LITERAL_SPAN_RE = re.compile(r"`[^`]+`|[\"'][^\"']+[\"']")
# A definitive "synthesize the rationale" ask = an explicit summary COMMAND whose DIRECT
# OBJECT is an abstract rationale noun ("summarize the rationale", "explain the tradeoffs").
# This must tip a tie toward vectors, past incidental graph / markdown meta-words
# ("related"/"file") that are mere objects of the question (not relations).
#
# The noun must be the command's object — only articles / determiners may sit between them.
# That keeps the bonus OFF a relational query that merely NAMES such a noun as an ENTITY:
# "summarize what imports the reasoning package" / "...the modules that depend on the
# rationale service" are graph asks (a strong relational verb governs the noun), so the noun
# is not the synth command's object and the bonus stays silent. A genuine "summarize the
# rationale of how X depends on Y" still fires (rationale IS the object).
_SYNTH_RATIONALE_RE = re.compile(
    r"\b(?:summar(?:y|ies|ize|ise)|explain|overview|gist)\b"
    r"(?:\s+(?:the|a|an|our|its|their|your|my|this|that|these|those"
    r"|all|whole|full|entire|key|main|overall|general))*"
    r"\s+(?:rationale|trade[\s-]?offs?|reasoning)\b",
    re.I,
)
_SYNTH_RATIONALE_BONUS = 1.0
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
    # Match INTENT on the literal-stripped text so a trigger word quoted/backticked as a
    # literal ("...said 'do not call this inside a loop'") does not fire its backend.
    intent = _LITERAL_SPAN_RE.sub(" ", query)
    if _GRAPH_RE.search(intent):
        scores[GRAPH] += _STRONG
    if _VECTOR_RE.search(intent):
        scores[VECTORS] += _STRONG
    if _LITERAL_RE.search(intent):
        scores[MARKDOWN] += _STRONG
    if _SYNTH_RATIONALE_RE.search(intent):
        # "summarize the rationale and tradeoffs" — a definitive synthesis ask whose OBJECT is
        # the rationale itself; tip a tie away from incidental graph/markdown meta-words toward
        # vectors. A relational query that only NAMES a rationale noun as an entity ("summarize
        # what imports the reasoning package") does NOT match here and keeps its graph route.
        scores[VECTORS] += _SYNTH_RATIONALE_BONUS
    # Code/quote tokens are scored on the FULL query (the literal the user named still counts).
    code_hits = len(_CODE_RE.findall(query)) + len(_QUOTED_RE.findall(query))
    if code_hits:
        scores[MARKDOWN] += min(2.0, float(code_hits)) * _TOKEN
    # a short keyword-ish query with no question/relational framing -> literal recall.
    # Count word-like tokens (not raw split) so empty / whitespace / punctuation-only
    # inputs score nothing here and fall to the semantic default instead of markdown.
    words = re.findall(r"[A-Za-z0-9]+", query)
    if 1 <= len(words) <= 3 and not _QUESTION_RE.search(query) and not _GRAPH_RE.search(intent):
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
# Semantic exemplar classifier (D016 accuracy profile / PR3b-2) — learned-style seam.
#
# Routes by MEANING, not lexical rules: embeds a small set of per-backend EXEMPLAR queries
# (the routing taxonomy) and the live query with the SAME injected encoder, then routes to the
# backend whose exemplars the query sits closest to (cosine). Slots behind the RouterClassifier
# seam (accuracy_profile) with NO [CONTRACT] change and never touches the default/speed (rule)
# path. The encoder is INJECTED (any text->vector callable, e.g. VoyageEmbedder) — paid at call
# time, never imported here. Offline char-n-gram encoders are LEXICAL, so the semantic win is
# demonstrable only with a real embedder (the captained D021 bake-off), never in CI — preserving
# the zero-dependency offline guarantee.
#
# EXEMPLARS are deliberately GENERIC (multi-domain, non-project) so routing a real query tests
# intent-generalization, not entity memorization. Authored by a blind multi-lens workflow; two
# graph items were de-leaked after an overlap check (a cross-lingual near-copy of the French GAP
# case + a near-duplicate of an English eval phrasing). See DECISION_LOG D021.
# --------------------------------------------------------------------------- #
DEFAULT_ROUTING_EXEMPLARS: dict[str, tuple] = {
    MARKDOWN: (
        "What exact value did we set the maximum retry attempts to in the payment retry config?",
        "Give me the full signature of the validateJwt() function, including its parameters and return type.",
        "Quote the error message the file upload handler throws when the MIME type isn't allowed.",
        "Which port does the cache service listen on in the staging environment?",
        "What header name does the rate limiter use to return the remaining-quota count?",
        "Paste the exact regex we use to validate phone numbers in the signup form.",
        "What's the name of the environment variable that holds the webhook signing secret?",
        "What did we set the connection pool size to for the analytics database replica?",
        "Tell me the exact cron expression for the nightly invoice reconciliation job.",
        "What's the default session token TTL, in seconds?",
    ),
    GRAPH: (
        "What services depend on the authentication API?",
        "Which background jobs call the payment processor?",
        "Do the new firewall rules conflict with the VPN configuration?",  # de-leaked (was cache/rate-limiter ~ French GAP)
        "Show me everything the checkout flow connects to.",
        "What upstream systems feed data into the search indexer?",
        "What other components pull in the email-sending library?",  # de-leaked (was 'which modules import' ~ eval phrasing)
        "How is the notification service wired to the message queue?",
        "If I change the user schema, what else breaks?",
        "Trace the call chain from the API gateway down to the inventory service.",
        "Which deployments depend on the shared config package?",
    ),
    VECTORS: (
        "Why did we end up choosing one message broker over another for the event pipeline?",
        "Give me a high-level summary of how our authentication flow works.",
        "What's the general thinking behind using optimistic locking instead of pessimistic locking here?",
        "Explain the tradeoffs of caching at the CDN edge versus in the application layer.",
        "What should I look at next to improve checkout reliability?",
        "What was the reasoning for splitting the monolith into separate billing and inventory services?",
        "Can you describe our philosophy around retries and idempotency?",
        "Remind me why we decided not to support offline writes in the mobile app.",
        "Summarize the main considerations when picking a rate limiting strategy for the public API.",
        "Broadly, how do we think about consistency versus availability in our data stores?",
    ),
}


def _cosine_sim(a, b) -> float:
    """Cosine similarity of two equal-length vectors (0.0 if either is degenerate)."""
    if len(a) != len(b):
        raise ValueError(f"embedding dim mismatch: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _encoder_accepts_input_type(embed: Callable) -> bool:
    """True if ``embed`` accepts a keyword ``input_type`` (query/document asymmetry).

    Mirrors the store's embed seam (``sqlite_store._embedder_accepts_input_type``) so the
    classifier embeds exemplars as ``"document"`` and the query as ``"query"`` for a
    query/document-aware encoder (e.g. Voyage), while a legacy one-arg ``text -> vector``
    callable is still called positionally. A ``**kwargs`` param qualifies; a param named
    ``input_type`` qualifies only when it is keyword-addressable alongside a leading text
    positional (so a sole/leading positional named ``input_type`` is treated as the text arg).
    """
    try:
        params = inspect.signature(embed).parameters
    except (TypeError, ValueError):  # builtins / C callables without a readable signature
        return False
    seen_positional = False
    for p in params.values():
        if p.kind is p.VAR_KEYWORD:
            return True
        if p.name == "input_type":
            if p.kind is p.KEYWORD_ONLY:
                return True
            if p.kind is p.POSITIONAL_OR_KEYWORD and seen_positional:
                return True
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
            seen_positional = True
    return False


class SemanticRouterClassifier:
    """A semantic, exemplar-based :class:`RouterClassifier` (D016 accuracy profile / PR3b-2).

    Embeds per-backend EXEMPLAR queries and the live query with the injected ``embed`` encoder,
    then routes to the backend whose exemplars the query is closest to (cosine, aggregated per
    backend by ``agg`` = ``"max"`` or ``"mean"``). ``margin`` is the top-two gap; ``details``
    carries the per-backend nearest exemplar (observability, like :meth:`Router.explain`).

    The encoder is injected (any ``text -> vector`` callable) and paid at call time, never
    imported here. Exemplars embed as ``input_type="document"`` and the query as ``"query"`` when
    the encoder accepts it (Voyage's asymmetry), else the legacy one-arg call. An empty query
    routes to the semantic default (mirrors :class:`RuleBasedClassifier`'s no-signal behavior).
    """

    name = "semantic-exemplar"

    def __init__(self, embed: Callable, *, exemplars: Optional[dict] = None,
                 priority: tuple = _PRIORITY, default_backend: str = VECTORS,
                 agg: str = "max") -> None:
        if agg not in ("max", "mean"):
            raise ValueError(f"agg must be 'max' or 'mean', got {agg!r}")
        src = exemplars if exemplars is not None else DEFAULT_ROUTING_EXEMPLARS
        self._exemplars = {b: tuple(qs) for b, qs in src.items()}
        if not self._exemplars or any(not qs for qs in self._exemplars.values()):
            raise ValueError("every backend needs >=1 exemplar")
        self._embed = embed
        self._priority = tuple(priority)
        self._default = default_backend
        self._agg = agg
        self._accepts_input_type = _encoder_accepts_input_type(embed)
        # Embed every exemplar once (as a document). For a real encoder this is the only batch of
        # calls at construction; for offline/mock encoders it is instant.
        self._vecs = {b: [self._embed_text(q, "document") for q in qs]
                      for b, qs in self._exemplars.items()}

    def _embed_text(self, text: str, input_type: str) -> list:
        if self._accepts_input_type:
            return self._embed(text, input_type=input_type)
        return self._embed(text)

    def classify(self, query: str) -> ClassificationResult:
        q = (query or "").strip()
        scores = {b: 0.0 for b in self._exemplars}
        if not q:
            return ClassificationResult(choice=self._default, scores=scores, margin=0.0,
                                        details={"classifier": self.name, "reason": "empty_query"})
        qv = self._embed_text(q, "query")
        nearest: dict = {}
        for b, vecs in self._vecs.items():
            sims = [_cosine_sim(qv, v) for v in vecs]
            best_i = max(range(len(sims)), key=sims.__getitem__)
            scores[b] = (sum(sims) / len(sims)) if self._agg == "mean" else sims[best_i]
            nearest[b] = self._exemplars[b][best_i]
        ordered = sorted(scores.values(), reverse=True)
        margin = ordered[0] - ordered[1] if len(ordered) > 1 else ordered[0]
        best = ordered[0]
        # tie-break by priority (matches RuleBasedClassifier); exact float ties are rare but
        # deterministic, and for a clean argmax only the winner equals ``best``.
        choice = next((b for b in self._priority if scores.get(b) == best),
                      max(scores, key=lambda b: scores[b]))
        return ClassificationResult(
            choice=choice, scores=scores, margin=margin,
            details={"classifier": self.name, "agg": self._agg,
                     "encoder": getattr(self._embed, "model", type(self._embed).__name__),
                     "nearest_exemplar": nearest},
        )


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
    # Per-query graph BFS depth the cascade injects into graph.search; None = the graph store's own
    # default (byte-equivalent). An accuracy profile sets it deeper to recover multi-hop gold (D032).
    graph_max_depth: Optional[int] = None


@dataclass(frozen=True)
class Consult2Config:
    """Cross-backend FUSION knobs — the accuracy end of the speed↔accuracy spectrum (D025/PLAN-7).

    When ``enabled``, :meth:`Router.route` returns a :class:`_FusionRetriever` that fans the query out
    to several backends and merges their ranked results into one top-k. ``method`` picks the merge:
    ``"rrf"`` (Reciprocal Rank Fusion — rank-based, robust to per-backend score-scale differences) or
    ``"score"`` (max-normalize each backend's scores to [0,1], then sum). ``rrf_k`` is the RRF damping
    constant; ``per_backend_k`` is the fan-out depth fetched from each backend before merging;
    ``backends`` names the backends to consult (empty = every registered backend). ``margin_below`` is
    reserved for a future *conditional* fusion (consult a second backend only when routing confidence is
    low); v1 always fuses across the fan-out set when ``enabled``. ``enabled=False`` is the default and
    keeps single-route/cascade behavior byte-for-byte unchanged.
    """

    enabled: bool = False
    margin_below: float = 0.0
    rrf_k: int = 60
    method: str = "rrf"             # "rrf" | "score"
    per_backend_k: int = 10         # fan-out depth fetched per backend before merge
    backends: tuple = ()            # backend names to fuse; () = every registered backend


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
    # Reranker = the post-retrieval re-scoring stage, run AFTER routing/fusion — read-orchestration is the
    # router's domain (the same place fusion lives), NOT the caller's. When set, `route()` wraps its
    # retriever so search over-fetches `rerank_top_n` candidates and the reranker re-scores them to k.
    # None (default) = no rerank, byte-for-byte today. Offline default is None (a lexical mock shows no
    # lift, D028); the real cross-encoder (`VoyageReranker`) is the captained path (D045).
    reranker: Optional[Any] = None
    rerank_top_n: int = 50
    embed: Optional[Any] = None
    embed_model: Optional[str] = None
    # Write-routing (D009: the router owns WHERE to STORE). markdown is the always-written literal
    # source-of-truth base (D001). Policies: "base_all" (base + vector + graph), "base_selective"
    # (base + the classify(content) backend), "single" (only classify(content)). Default = base_all:
    # D023 measured the write→retrieve round-trip and found selective placement loses ~30% recall
    # because content- and query-classification diverge under the rule classifier — so the recall-safe
    # default writes every index. route_write() is a NEW method, so RouterConfig() *retrieval*
    # behavior is byte-for-byte unchanged regardless of this field.
    write_policy: str = "base_all"
    write_base: str = MARKDOWN
    # Dedup-on-write (ADR-P2/P4, D024): on write, if an existing memory in `dedup_backend` is at or
    # above `dedup_threshold` cosine similarity, MERGE into it (reuse its id, newer content wins,
    # version+1) instead of creating a duplicate. **Default OFF** — D024 measured that the offline
    # char-n-gram embedder CANNOT separate near-dups from distinct-but-similar memories (a distinct
    # "read timeout 5s" vs "write timeout 30s" scores HIGHER than a reworded true duplicate), so
    # auto-merging offline risks FALSE MERGES = silent data loss. So dedup is intended for the
    # real-embedder (paid) path, where same-fact vs different-fact actually separate (the D020 story);
    # `RouterConfig(dedup=True)` is permitted (not enforced — the router can't tell a "real" embedder
    # from the hashing default) but is unsafe offline. `dedup_threshold` applies when enabled. Only
    # Router.write uses these; route()/route_write() are unaffected.
    dedup: bool = False
    dedup_threshold: float = 0.92
    dedup_backend: str = VECTORS


@dataclass(frozen=True)
class WriteReceipt:
    """The result of :meth:`Router.write`: the resulting ``item_id`` (an existing one if a duplicate
    was merged), whether it ``merged``, the ``version`` written, and the backend names persisted to."""

    item_id: str
    merged: bool
    version: int
    backends: tuple


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
                     embed_model: Optional[str] = None, k: int = 8,
                     graph_max_depth: int = 3, reranker: Optional[Any] = None,
                     rerank_top_n: int = 50) -> RouterConfig:
    """The ``accuracy`` profile: injected classifier + real embedder, cascade ON.

    The heavy strategies are CALLER-INJECTED (PR3): ``classifier`` is a
    :class:`RouterClassifier` (e.g. a learned / spaCy classifier) and ``embed`` is
    a real embedder the vector store is built around. This factory only *builds the
    config* — it turns the graph→vector cascade on (traversing the graph to
    ``graph_max_depth`` hops, default 3 — one deeper than speed's 2, a PROVISIONAL
    value pending a captained "does deeper traversal help" measurement, D032) and
    leaves ``consult2`` at its declared default (no RRF / second-opinion ships here).
    ``k`` is the profile's retrieval breadth (wider than speed's default 5). An optional
    ``reranker`` adds the post-retrieval cross-encoder re-scoring stage (D045); ``None`` = no rerank.
    """
    return RouterConfig(
        profile_name="accuracy",
        classifier=classifier,
        cascade=CascadeConfig(enabled=True, graph_max_depth=graph_max_depth),
        embed=embed,
        embed_model=embed_model,
        k=k,
        reranker=reranker,
        rerank_top_n=rerank_top_n,
    )


def accuracy_local_profile(*, classifier: RouterClassifier, embed: Any,
                           embed_model: Optional[str] = None, k: int = 8,
                           graph_max_depth: int = 3, reranker: Optional[Any] = None,
                           rerank_top_n: int = 50) -> RouterConfig:
    """Opt-in local accuracy profile for MiniLM + sqlite-vec experiments.

    Same routing shape as ``accuracy_profile`` but with a distinct profile name so
    callers can select local semantic retrieval without implying a paid Voyage run.
    """
    config = accuracy_profile(
        classifier=classifier,
        embed=embed,
        embed_model=embed_model,
        k=k,
        graph_max_depth=graph_max_depth,
        reranker=reranker,
        rerank_top_n=rerank_top_n,
    )
    return replace(config, profile_name="accuracy-local")


def fusion_profile(*, method: str = "rrf", per_backend_k: int = 10, rrf_k: int = 60,
                   k: int = 8, backends: tuple = (), reranker: Optional[Any] = None,
                   rerank_top_n: int = 50) -> RouterConfig:
    """A cross-backend FUSION profile: fan out to several backends and merge their results (D025).

    Enables :class:`Consult2Config` so :meth:`Router.route` returns a :class:`_FusionRetriever`.
    ``method`` is ``"rrf"`` (Reciprocal Rank Fusion) or ``"score"`` (max-normalized score fusion);
    ``per_backend_k`` is the fan-out depth fetched per backend; ``backends`` selects which to fuse
    (empty = all registered). The cascade is left OFF (fusion supersedes it). An optional ``reranker``
    adds the post-fusion cross-encoder re-scoring stage — the captained "fuse-all, then rerank" (D045);
    ``None`` (default) = fusion only. Single-route/speed and the offline default are unaffected.
    """
    return RouterConfig(
        profile_name="fusion",
        consult2=Consult2Config(enabled=True, method=method, rrf_k=rrf_k,
                                per_backend_k=per_backend_k, backends=backends),
        k=k,
        reranker=reranker,
        rerank_top_n=rerank_top_n,
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

    def _graph_search_kwargs(self, kwargs: dict) -> dict:
        """Inject the cascade's configured graph BFS depth (if any) into a graph.search call, so the
        accuracy profile traverses deeper without rebuilding the store (D032). An explicit caller
        ``max_depth`` wins; ``graph_max_depth=None`` leaves the graph store's own default untouched.

        This also feeds the GATE's graph stage. The accept/fall-through verdict is invariant for any
        depth **>= 1**: deeper traversal only appends strictly-lower-scored farther nodes, so rank-0 and
        rank-1 — hence ``score0`` and ``margin = score0 - score1`` — are unchanged; the shipping
        speed(2)/accuracy(3) band never flips a verdict. NOTE: a future ``graph_max_depth=0`` profile would
        remove rank-1 and could shrink the margin — revisit the gate floors if a depth-0 profile is added."""
        gk = dict(kwargs)
        if self._cfg.graph_max_depth is not None:
            gk.setdefault("max_depth", self._cfg.graph_max_depth)
        return gk

    # -- MemoryStore protocol ----------------------------------------------
    def search(self, query: str, *, k: int = 5, as_of: Optional[float] = None,
               **kwargs: Any) -> list[RetrievedItem]:
        """Graph→vector cascade retrieval for ``query`` (gate + projection).

        Backend-specific ``**kwargs`` (the ``MemoryStore.search`` seam) are forwarded to both
        underlying stores so a routed read through the cascade honors them exactly as a direct
        single-backend read does (e.g. a ``RouterStore`` over a cascade-enabled profile).
        """
        graph_hits = self._graph.search(query, k=k, as_of=as_of, **self._graph_search_kwargs(kwargs))
        gate = self._gate(query, graph_hits, as_of)
        if gate.decision == ACCEPT:
            return self._project(graph_hits)
        return self._vector.search(query, k=k, as_of=as_of, **kwargs)

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

    def delete(self, item_id: str) -> bool:
        raise NotImplementedError(
            "_GraphVectorCascade is the retrieval-only view returned by Router.route(); "
            "delete from the underlying graph/vector backends directly (or via Router.delete)."
        )

    # -- gate + projection -------------------------------------------------
    def gate(self, query: str, *, k: int = 5, as_of: Optional[float] = None) -> GateResult:
        """Introspect the accept/fall-through verdict without retrieving (testable seam)."""
        return self._gate(query, self._graph.search(query, k=k, as_of=as_of,
                                                     **self._graph_search_kwargs({})), as_of)

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


_FUSION_METHODS = frozenset({"rrf", "score"})


class _FusionRetriever:
    """Retrieval-only view that fans a query out to several backends and FUSES their ranked results
    into one top-k — the accuracy end of the routing spectrum (returned by :meth:`Router.route` when a
    profile enables :class:`Consult2Config`). Two merge methods (``Consult2Config.method``):

    * ``"rrf"`` — Reciprocal Rank Fusion: an item's fused score is ``sum over backends of
      1/(rrf_k + position)`` (1-based position). Rank-based, so it is robust to per-backend score-scale
      differences (markdown BM25 is unbounded; vector cosine is [0,1]).
    * ``"score"`` — score-normalization fusion: max-normalize each backend's scores to [0,1], then sum.

    Fan-out covers ``consult2.backends`` (default: every registered backend); each is searched to
    ``per_backend_k`` depth, results merged + de-duplicated by ``item_id``, then the top-k returned
    (rank reset 0..k-1). The returned-token budget is unchanged (still k items) — fusion buys recall at
    an equal retrieval-context cost, paying N× backend searches in compute. ``write`` raises (a view).
    """

    def __init__(self, backends: dict, consult2: "Consult2Config") -> None:
        if consult2.method not in _FUSION_METHODS:
            # Fail loud on a typo'd method rather than silently routing as one or the other.
            raise ValueError(
                f"_FusionRetriever supports method in {sorted(_FUSION_METHODS)}; got {consult2.method!r}")
        self._backends = backends
        self._cfg = consult2

    # -- MemoryStore protocol ----------------------------------------------
    def search(self, query: str, *, k: int = 5, as_of: Optional[float] = None,
               **kwargs: Any) -> list[RetrievedItem]:
        """Fan ``query`` out to the configured backends and return the fused top-``k``."""
        if k <= 0:
            return []
        depth = max(k, self._cfg.per_backend_k)
        per_backend = [
            self._backends[name].search(query, k=depth, as_of=as_of, **kwargs)
            for name in self._fan_out_names()
        ]
        fused = (self._rrf(per_backend) if self._cfg.method == "rrf"
                 else self._score_fusion(per_backend))
        return [RetrievedItem(item=item, score=score, rank=rank)
                for rank, (item, score) in enumerate(fused[:k])]

    def get(self, item_id: str) -> Optional[MemoryItem]:
        for name in self._fan_out_names():
            found = self._backends[name].get(item_id)
            if found is not None:
                return found
        return None

    def all(self) -> list[MemoryItem]:
        seen: set = set()
        out: list = []
        for name in self._fan_out_names():
            for item in self._backends[name].all():
                if item.item_id not in seen:
                    seen.add(item.item_id)
                    out.append(item)
        return out

    def write(self, item: MemoryItem) -> None:
        raise NotImplementedError(
            "_FusionRetriever is the retrieval-only view returned by Router.route(); "
            "write to the underlying backends directly.")

    def delete(self, item_id: str) -> bool:
        raise NotImplementedError(
            "_FusionRetriever is the retrieval-only view returned by Router.route(); "
            "delete from the underlying backends directly (or via Router.delete).")

    # -- fusion ------------------------------------------------------------
    def _fan_out_names(self) -> list:
        """Registered backend names to fuse (``consult2.backends`` ∩ registered, else all registered),
        de-duplicated + order-preserving — a name repeated in the config must NOT double-count a backend."""
        names = self._cfg.backends or tuple(self._backends.keys())
        out: list = []
        for n in names:
            if n in self._backends and n not in out:
                out.append(n)
        return out

    def _rrf(self, per_backend: list) -> list:
        """Reciprocal Rank Fusion across each backend's ranked hits; merged best-first."""
        agg: dict = {}  # item_id -> [fused_score, item]
        for hits in per_backend:
            for position, hit in enumerate(hits):
                contrib = 1.0 / (self._cfg.rrf_k + position + 1)  # 1-based rank (canonical RRF)
                if hit.item_id in agg:
                    agg[hit.item_id][0] += contrib
                else:
                    agg[hit.item_id] = [contrib, hit.item]
        return self._sorted(agg)

    def _score_fusion(self, per_backend: list) -> list:
        """Max-normalized score fusion: each backend's scores scaled to [0,1], then summed.

        Negative scores are clamped to 0 BEFORE normalizing: a lexical/hashing backend can emit a
        negative cosine, and a raw negative contribution would PENALIZE an item for appearing (weakly)
        in another backend — the opposite of fusion. Clamping makes every contribution a nonnegative
        [0,1] vote. (RRF sidesteps this entirely — it is rank-based, not score-based.)
        """
        agg: dict = {}
        for hits in per_backend:
            top = max((h.score for h in hits), default=0.0)
            denom = top if top > 0 else 1.0
            for hit in hits:
                norm = max(0.0, hit.score) / denom
                if hit.item_id in agg:
                    agg[hit.item_id][0] += norm
                else:
                    agg[hit.item_id] = [norm, hit.item]
        return self._sorted(agg)

    @staticmethod
    def _sorted(agg: dict) -> list:
        """``[(item, fused_score), ...]`` sorted by descending score, ties stable by ``item_id``."""
        merged = [(entry[1], entry[0]) for entry in agg.values()]
        merged.sort(key=lambda t: (-t[1], t[0].item_id))
        return merged


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
        """Return the store for ``query`` — the routed retriever, with the profile's rerank stage applied.

        Resolves the base retriever (fusion / cascade / single-route — see :meth:`_base_route`); then,
        when the active profile sets a ``reranker``, wraps it so ``search`` over-fetches ``rerank_top_n``
        candidates and the reranker re-scores them to ``k``. Reranking is the router's read-orchestration,
        the same as fusion — the caller (the plugin) never wires a reranker; the *profile* does.
        """
        retriever = self._base_route(query)
        if self._config.reranker is not None:
            from .stores.rerankers import RerankedStore  # lazy: avoid a router<->stores import cycle
            retriever = RerankedStore(retriever, self._config.reranker,
                                      rerank_top_n=self._config.rerank_top_n)
        return retriever

    def _base_route(self, query: str) -> MemoryStore:
        """The routed retriever BEFORE any rerank, degrading to an available backend.

        Fusion (``consult2``) takes precedence and returns a fresh :class:`_FusionRetriever` (fan-out +
        merge). Else the cascade for a GRAPH-classified query with both cascade backends registered
        returns a fresh :class:`_GraphVectorCascade`; otherwise the v1 ``(choice, *fallback)`` single-route
        resolution.
        """
        choice = self.classify(query)
        if self._config.consult2.enabled and self.backends:
            return self._fusion_retriever()
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

    def _fusion_retriever(self) -> _FusionRetriever:
        """Construct a fresh :class:`_FusionRetriever` over the CURRENTLY registered backends.

        Not memoized (same reasoning as the cascade): ``self.backends`` may be replaced after the
        Router is built, and each fused route must fan out to whatever backends are registered NOW.
        """
        return _FusionRetriever(self.backends, self._config.consult2)

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

    # -- write-routing (D009: the router owns WHERE to STORE, not just retrieval) -----------
    def route_write(self, item: MemoryItem) -> list[MemoryStore]:
        """Return the backend store(s) to PERSIST ``item`` in, per the active write policy.

        Mirrors :meth:`route` for the write path (D009: the router owns *where & how* to store).
        The caller writes the item into each returned store
        (``for s in router.route_write(item): s.write(item)``). Markdown is the always-written
        literal source-of-truth base (D001); :attr:`RouterConfig.write_policy` decides the secondary
        index(es). Degrades to whatever backends are registered; raises only if none are.
        """
        stores: list[MemoryStore] = []
        for name in self._write_backend_names(item):
            store = self.backends.get(name)
            if store is not None and store not in stores:
                stores.append(store)
        if not stores:
            raise RuntimeError("Router has no registered backends to write to")
        return stores

    def write_plan(self, item: MemoryItem) -> list[str]:
        """The backend NAMES :meth:`route_write` would persist ``item`` to (introspection seam)."""
        return self._write_backend_names(item)

    def _write_backend_names(self, item: MemoryItem) -> list[str]:
        policy = self._config.write_policy
        base = self._config.write_base
        content = item.content or ""
        if policy == "base_all":  # recall-safe default: base + every secondary index
            secondary = [MARKDOWN, VECTORS, GRAPH]
            if FTS5 in self.backends:  # lexical index is optional — include it only when wired in
                secondary.append(FTS5)
            names = [base, *(n for n in secondary if n != base)]
        elif policy == "base_selective":  # base + only the classify(content) backend
            choice = self._classifier.classify(content).choice
            names = [base] + ([choice] if choice != base else [])
        elif policy == "single":  # only the classify(content) backend (no base)
            names = [self._classifier.classify(content).choice]
        else:
            raise ValueError(f"unknown write_policy {policy!r}")
        out: list[str] = []  # dedupe, preserve order
        for name in names:
            if name not in out:
                out.append(name)
        return out

    # -- dedup-aware orchestrated write (ADR-P2/P4, D024) ------------------------------------
    def write(self, item: MemoryItem, *, dedup: Optional[bool] = None) -> WriteReceipt:
        """Persist ``item`` with dedup + write-routing; return a :class:`WriteReceipt`.

        The orchestrated write the ADR describes: resolve dedup (a near-duplicate MERGES into the
        existing memory — reuse its id, newer content wins, version+1), then route the (possibly
        merged) item to its backend(s) and persist. Returns the resulting id so the caller learns
        whether a duplicate was merged. ``dedup`` overrides :attr:`RouterConfig.dedup` for this call.
        The merge path copies via ``dataclasses.replace`` (the caller's item is untouched); the
        no-merge path hands the original ``item`` to the backend stores, which may set derived fields
        (e.g. ``tokens``) per their own write behavior.
        """
        merged = False
        do_dedup = self._config.dedup if dedup is None else dedup
        if do_dedup:
            found = self._find_duplicate(item)
            if found is not None:
                dup_id, dup_version = found
                item = replace(item, item_id=dup_id, version=dup_version + 1)  # newer content wins
                merged = True
        stores = self.route_write(item)
        for store in stores:
            store.write(item)
        names = tuple(n for n in self._write_backend_names(item) if n in self.backends)
        return WriteReceipt(item_id=item.item_id, merged=merged, version=item.version, backends=names)

    def _find_duplicate(self, item: MemoryItem) -> Optional[tuple]:
        """``(existing_id, existing_version)`` of a near-duplicate of ``item`` in the dedup backend
        (top non-self hit at/above ``dedup_threshold``), else ``None``.

        A FALSE positive collapses two distinct memories (silent data loss), so the threshold is
        conservative. Writing over the SAME id is a plain overwrite, not a dedup-merge, so a self-hit
        is skipped. Hits are score-sorted, so the first non-self hit decides.
        """
        store = self.backends.get(self._config.dedup_backend)
        content = (item.content or "").strip()
        if store is None or not content:
            return None
        for hit in store.search(content, k=2):
            if hit.item_id == item.item_id:
                continue  # overwrite of the same memory, not a duplicate to merge
            if hit.score >= self._config.dedup_threshold:
                return (hit.item_id, hit.item.version)
            return None  # top non-self hit is below threshold -> no near-duplicate
        return None

    # -- delete (unconditional fan-out; ADR-P9 retention primitive) --------------------------
    def delete(self, item_id: str) -> int:
        """Delete ``item_id`` from EVERY registered backend; return how many removed it.

        Write-routing is policy-driven, but delete is **unconditional and complete**: under ``base_all`` an
        item lives in several backends, so a correct delete clears all of them. Idempotent — a backend that
        doesn't have the id is a no-op. ``delete`` is now part of the ``MemoryStore`` protocol, so every
        registered backend implements it; the ``getattr`` check below stays only as defense for a non-store
        object placed in ``backends``. Returns the per-backend count; the ``MemoryStore`` facade
        (:meth:`RouterStore.delete`) collapses it to a bool.
        """
        removed = 0
        seen: set = set()
        for store in self.backends.values():
            if id(store) in seen:           # the same store may be registered under more than one name
                continue
            seen.add(id(store))
            deleter = getattr(store, "delete", None)
            if callable(deleter) and deleter(item_id):
                removed += 1
        return removed


class RouterStore:
    """A :class:`~memeval.protocols.MemoryStore` facade over a :class:`Router` — makes routed
    write-routing LIVE (D025).

    The Router owns *where* to store and read, but it is NOT itself a store: :meth:`Router.write`
    returns a :class:`WriteReceipt` (not ``None``), :meth:`Router.route` returns a *backend* (not
    results), and the Router has no ``get`` / ``all``. So nothing that expects a ``MemoryStore`` — the
    plugin ``_Engine``, the harness ``MemoryFramework``, the #63 native eval ``store=`` seam — can use
    the Router's dedup + multi-index write path; every such write goes direct to a single backend and
    write-routing/dedup stay dead code. ``RouterStore`` adapts the Router to the five-method protocol so
    those seams drive routed writes and reads unchanged. Purely additive: the Router contract is
    untouched; this only re-shapes it.

    Semantics:
      * ``write(item)`` -> :meth:`Router.write` (dedup -> route_write -> fan to every policy backend),
        discarding the receipt to honor the ``-> None`` protocol. The receipt is kept on
        :attr:`last_receipt` for callers (e.g. an integration test) that want the fan-out/merge result.
      * ``search`` routes per call: ``route(query).search(query, k=, as_of=)`` — the routed read,
        preserving ``as_of`` no-future-peeking and any backend kwargs.
      * ``get`` / ``all`` union the registered backends, de-duplicating by ``item_id`` first-seen in
        backend priority order (``base_all`` writes the same item to several backends, so the fan-out
        copies must collapse on read). Markdown (the literal source-of-truth base, D001) is scanned
        first so its revision wins ties.
    """

    #: get/all scan + de-dup order (markdown base first — the literal source of truth).
    _READ_ORDER = (MARKDOWN, VECTORS, GRAPH)

    def __init__(self, router: Router) -> None:
        self._router = router
        self.last_receipt: Optional[WriteReceipt] = None

    def write(self, item: MemoryItem) -> None:
        """Persist ``item`` through the Router (dedup + write-routing); discard the receipt."""
        self.last_receipt = self._router.write(item)

    def get(self, item_id: str) -> Optional[MemoryItem]:
        """Return the item from the first backend (priority order) that has it, else ``None``."""
        for name in self._ordered_backend_names():
            store = self._router.backends.get(name)
            if store is not None:
                found = store.get(item_id)
                if found is not None:
                    return found
        return None

    def search(self, query: str, *, k: int = 5, as_of: Optional[float] = None,
               **kwargs: Any) -> list[RetrievedItem]:
        """Route ``query`` to its backend and return that backend's top-``k`` (routed read)."""
        return self._router.route(query).search(query, k=k, as_of=as_of, **kwargs)

    def all(self) -> list[MemoryItem]:
        """Every stored item across backends, de-duplicated by ``item_id`` (collapses fan-out copies)."""
        seen: set[str] = set()
        out: list[MemoryItem] = []
        for name in self._ordered_backend_names():
            store = self._router.backends.get(name)
            if store is None:
                continue
            for item in store.all():
                if item.item_id not in seen:
                    seen.add(item.item_id)
                    out.append(item)
        return out

    def delete(self, item_id: str) -> bool:
        """Delete ``item_id`` from every backend via the Router; return ``True`` if it was present in any
        (idempotent). The ``MemoryStore`` contract is a bool "was it there"; the per-backend COUNT is on
        :meth:`Router.delete` — mirroring write (``Router.write`` returns a ``WriteReceipt`` while
        ``RouterStore.write`` returns ``None``)."""
        return self._router.delete(item_id) > 0

    def _ordered_backend_names(self) -> list[str]:
        """Registered backend names in read priority (``_READ_ORDER`` first, then any extras)."""
        names = [n for n in self._READ_ORDER if n in self._router.backends]
        names += [n for n in self._router.backends if n not in names]
        return names


__all__ = [
    "Router", "RouterStore", "RouterConfig", "CascadeConfig", "Consult2Config", "WriteReceipt",
    "speed_profile", "accuracy_profile", "accuracy_local_profile", "fusion_profile",
]
