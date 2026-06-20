"""Intelligent router — owner: Brent (@bgibson1618). Query dispatch over the stores.

Classifies a memory query and routes it to the SINGLE best backend instead of
fanning out: relationship/contradiction queries -> graph, conceptual/"why"
queries -> vectors, literal keyword/identifier lookups -> markdown. Rule-based and
deterministic (v1); a learned upgrade (a fine-tuned local model) slots behind the
same `route()` signature later (DECISION_LOG D007).

Division of labor (DECISION_LOG D009): the PRIMARY AGENT decides *if* to retrieve;
the router owns *where & how*. So cascade/fall-through across backends is the
router's concern, not the caller's. v1 is single-route + graceful degradation to an
available backend (D003); the cascade/meta-index (D008) grows here later.

Approach: cheap signal functions contribute to a per-backend score; argmax wins
(ties + no-signal -> the semantic default). The top-two margin is a ready-made
"routing confidence" / fusion-trigger signal (see `explain`). Stdlib only,
deterministic, no network.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .protocols import MemoryStore

GRAPH = "graph"
VECTORS = "vectors"
MARKDOWN = "markdown"

# Signal weights. Intent predicates are strong; code tokens are modest so a
# semantic "why <code_token>" still routes to vectors, not markdown.
_STRONG = 3.0
_TOKEN = 1.0
_SHORT = 1.5

# Relationship / traversal intent -> graph.
_GRAPH_RE = re.compile(
    r"\bdepends?\s+on\b|\bdepend(?:s|ency|encies)?\b"
    r"|\bcall(?:s|ed|ing)?\b|\buses?\b|\busing\b|\bused\s+by\b"
    r"|\bconnect(?:s|ed|ion)?\s+to\b|\bconnected\b"
    r"|\brelate[sd]?\s+to\b|\brelated\b|\brelationship\b"
    r"|\bconflicts?\s+with\b|\bcontradicts?\b"
    r"|\bcompare[sd]?\b|\bbetween\b.+\band\b|\blinked?\s+to\b",
    re.I,
)

# Conceptual / rationale / synthesis intent -> vectors. Overrides surface code tokens.
_VECTOR_RE = re.compile(
    r"\bwhy\b|\bhow\s+come\b|\breason(?:ing|s|ed)?\b|\brationale\b"
    r"|\bsummar(?:y|ies|ize|ise)\b|\bexplain\b|\boverview\b"
    r"|\btrade[\s-]?offs?\b|\bdecid(?:e|ed|es|ing)\b|\bdecision\b"
    r"|\bchose\b|\bchoose\b|\bchoosing\b|\bthoughts?\s+on\b|\bapproach\b",
    re.I,
)

# Literal-lookup intent -> markdown, even inside a question.
_LITERAL_RE = re.compile(
    r"\b(?:exact\s+)?name\s+of\b|\bvalue\s+of\b|\bsignature\b"
    r"|\bdefinition\s+of\b|\bdefined\b|\bspelling\b|\bfile\b",
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
# Graceful-degradation order when the chosen backend isn't registered (D003).
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
    # a short keyword-ish query with no question/relational framing -> literal recall
    if len(query.split()) <= 3 and not _QUESTION_RE.search(query) and not _GRAPH_RE.search(query):
        scores[MARKDOWN] += _SHORT
    return scores


class Router:
    """Routes a query to one registered :class:`MemoryStore` backend (rule-based v1).

    ``backends`` maps a name (``"graph"`` / ``"vectors"`` / ``"markdown"``) to a
    concrete store. :meth:`classify` is the pure routing decision (testable without
    backends); :meth:`route` resolves it to a registered store, degrading gracefully
    when the chosen backend is absent.
    """

    def __init__(self, backends: Optional[dict[str, MemoryStore]] = None) -> None:
        self.backends = backends or {}

    def classify(self, query: str) -> str:
        """Return the best-fit backend name for ``query`` (no backends required)."""
        scores = _score(query or "")
        best = max(scores.values())
        if best <= 0.0:
            return VECTORS  # no signal -> semantic default
        for name in _PRIORITY:  # deterministic tie-break
            if scores[name] == best:
                return name
        return VECTORS

    def explain(self, query: str) -> dict[str, Any]:
        """Decision + scores + top-two margin (a routing-confidence / fusion signal)."""
        scores = _score(query or "")
        ranked = sorted(scores.values(), reverse=True)
        margin = ranked[0] - ranked[1] if len(ranked) > 1 else ranked[0]
        return {"choice": self.classify(query), "scores": scores, "margin": margin}

    def route(self, query: str, **kwargs: Any) -> MemoryStore:
        """Return the store for ``query``, degrading to an available backend (D003)."""
        choice = self.classify(query)
        for name in (choice, *_FALLBACK):
            store = self.backends.get(name)
            if store is not None:
                return store
        raise RuntimeError("Router has no registered backends to route to")


__all__ = ["Router"]
