"""Relation vocabulary + direction model for the graph store — owner: Brent (@bgibson1618).

Graph edges are DIRECTED and TYPED. v1 OKF links were untyped (the anchor text was discarded); this
module is the shared classifier that turns a relation phrase — an edge's OKF anchor text, or a query's
intent — into a closed-enum relation, plus the traversal DIRECTION each relation implies. It is the one
vocabulary both edge-typing (at write) and query-intent (at search) key off, so they can't drift.

The closed enum mirrors the router's `_GRAPH_RE` terms (depends-on / calls / uses / imports /
conflicts-with / contradicts / renames / impacts), with `relates_to` as the **generic default** when no
relation verb is present. ``query_intent`` is a **best-effort heuristic** for the common forward/reverse
phrasings (covered + regression-tested); exhaustive natural-language direction parsing is out of scope —
that is the learned-router north-star's job. It resolves a direction only when the phrasing is clearly
forward or clearly reverse; an **ambiguous** direction degrades to recall-safe **both-way** traversal
(D023 ethos — never silently drops the incoming-edge gold; at worst lower precision), never to a
wrong-relation result. ``relates_to`` is special: a generic edge is traversed by ANY relational query
(it matches every intent, both directions), so an untyped corpus behaves exactly as the pre-typed store —
typed filtering only kicks in for edges that actually carry a type. Stdlib only; no dependencies.
"""

from __future__ import annotations

import re

# -- relation enum (string constants) ------------------------------------------
DEPENDS_ON = "depends_on"
CALLS = "calls"
USES = "uses"
IMPORTS = "imports"
CONFLICTS_WITH = "conflicts_with"
CONTRADICTS = "contradicts"
RENAMES = "renames"
IMPACTS = "impacts"
RELATES_TO = "relates_to"  # generic default — traversed by any query, both directions

# -- traversal direction -------------------------------------------------------
OUT = "out"     # follow the edge from source -> target (X depends_on Y: Y is X's OUT neighbor)
IN = "in"       # follow target -> source (who depends_on X: X's IN neighbors)
BOTH = "both"   # symmetric

# Each relation's DEFAULT traversal direction (for an edge / a forward query).
_TRAVERSAL = {
    DEPENDS_ON: OUT, CALLS: OUT, USES: OUT, IMPORTS: OUT, RENAMES: OUT, IMPACTS: OUT,
    CONFLICTS_WITH: BOTH, CONTRADICTS: BOTH, RELATES_TO: BOTH,
}

# Anchor/query phrase -> relation. Ordered: first match wins. Patterns are broad enough to catch the
# noun/verb forms that appear in OKF anchors AND in natural-language queries ("depends on", "dependency",
# "callee", "conflict").
_REL_PATTERNS = (
    (re.compile(r"\bdepend"), DEPENDS_ON),                 # depends on / dependency / dependents
    (re.compile(r"\bconflict"), CONFLICTS_WITH),
    (re.compile(r"\bcontradict"), CONTRADICTS),
    (re.compile(r"\bimport(s|ed|ing)?\b"), IMPORTS),  # bounded: must NOT match "important"
    (re.compile(r"\bcall(s|ed|ing|er|ee)?\b|\bcallee\b|\bcaller\b"), CALLS),
    (re.compile(r"\buse[sd]?\b|\busing\b|\buses\b"), USES),
    # impacts/"what breaks" is checked BEFORE renames so "what breaks if I rename X" reads as an impact
    # query (what breaks), not a rename query. Bounded so "important" never reads as impact.
    (re.compile(r"\bimpact(s|ed|ing)?\b|\baffect(s|ed|ing)?\b|\bwhat breaks\b"), IMPACTS),
    (re.compile(r"\brenam(e|ed|es|ing)\b"), RENAMES),
    (re.compile(r"\brelate|\brelationship\b|\bconnected\b|\blinked\b"), RELATES_TO),
)

# Query phrases that REVERSE a forward (OUT) relation to IN — asking for the SOURCES/dependents/impact
# rather than the targets. ("dependents" -> IN, but "dependency" -> OUT; the pattern matches the former.)
# IN = the queried entity is the OBJECT of the relation verb (asking for the SOURCES pointing at it):
# "what depends on X", "modules that depend on X", "services depending on X", "which modules import X",
# "who uses X", "used by", "what impacts X", "impacted by", "what breaks". OUT = the entity is the SUBJECT
# ("what does X depend on", "X's dependency", "what does X impact") — those must NOT match here. The
# what/which/who alternation allows at most ONE word before the verb ("which modules import"), so the
# subject form "what DOES X depend on" (two words: "does X") stays OUT.
# SUBJECT form -> forces OUT, checked BEFORE the IN-signal. A wh-question whose auxiliary is followed by
# an ENTITY word and then the relation verb: the entity is the SUBJECT, so the query wants its targets.
# "what does X depend on" / "what is X depending on" / "who is X calling" / "what are X's dependencies".
# Contrast "who is calling X" (verb right after the auxiliary -> entity is the object) which does NOT
# match here and falls through to the IN-signal.
_SUBJECT_FORM = re.compile(
    r"\b(what|which|who|whose)\s+(is|are|was|were|does|do|did|will|would|should|can|could)\s+"
    r"\w+\s+(\w+\s+)?(depend|call|import|use|using|rely|impact|affect|conflict|contradict|renam)")

# IN = the queried entity is the OBJECT of the relation verb (asking for the SOURCES pointing at it):
# "what depends on X", "modules that depend on X", "services depending on X", "which modules import X",
# "who uses X", "used by", "what impacts X", "impacted by", "what breaks".
_IN_SIGNAL = re.compile(
    r"\bdependents?\b|\bcallers?\b|\bimporters?\b"
    r"|\bthat\s+(depends?|calls?|imports?|uses?)\b"                       # "modules that depend/call/…"
    r"|\b(depending|calling|importing|using)\b"                          # "services depending on"
    r"|\b(what|which|who)\s+(\w+\s+)?(depend(s|ed)?\s+on|call(s|ed)?|import(s|ed)?|use[sd]?)\b"  # incl. past tense: "which modules imported X"
    r"|\b(depended\s+on|called|imported|used)\s+by\b"                    # passive
    r"|\bwhat\s+(\w+\s+)?(impacts?|affects?)\b|\bimpacted\s+by\b|\baffected\s+by\b"
    r"|\bupstream\b|\breverse\b")

# OUT = the queried entity is the SUBJECT — terse/idiomatic forward forms the SUBJECT_FORM (a wh-question)
# doesn't cover: "X's dependency", "X callee", and "what breaks if X changes" (= what X IMPACTS, the OUT
# side of an impacts edge — NOT a reverse query).
# Checked AFTER _IN_SIGNAL, so reverse forms ("modules that depend on X", "depending on") are already
# claimed for IN; what remains here is a forward subject-verb ("Zephyr depends on", "Handler calls").
_OUT_SIGNAL = re.compile(
    r"\bdependenc(y|ies)\b|\bcallee\b|\bwhat\s+breaks\b|\bbreaks?\s+if\b"
    r"|\b\w+\s+(depends?\s+on|calls?|imports?|uses?)\b")


# Code-identifier delimiters (backticks, quotes, dots, slashes, underscores, brackets, hyphens) are
# flattened to spaces before direction parsing, so a backticked/dotted identifier reads as a plain word:
# "who is `Handler` calling" / "what does `schema.py` depend on" parse like their bare-token forms.
_DELIMS = re.compile(r"[`'\".,:;/\\_()\[\]{}<>-]+")


def _normalize(text: str) -> str:
    """Lowercase + flatten code-identifier delimiters to single spaces (for query direction parsing)."""
    return re.sub(r"\s+", " ", _DELIMS.sub(" ", (text or "").lower())).strip()


# "what breaks" / "breaks if" is an impact-INTENT override: the query is about breakage even when it also
# names another verb ("what breaks if AuthService uses SessionCache" is impacts, not uses).
_BREAKS_OVERRIDE = re.compile(r"\bwhat breaks\b|\bbreaks?\s+if\b")


def classify_relation(text: str) -> str:
    """Map an anchor/query phrase to a closed-enum relation; ``relates_to`` if no relation verb is found."""
    t = (text or "").lower()
    if _BREAKS_OVERRIDE.search(t):
        return IMPACTS
    for pat, rel in _REL_PATTERNS:
        if pat.search(t):
            return rel
    return RELATES_TO


def edge_direction(relation: str) -> str:
    """The default traversal direction for an EDGE of this relation."""
    return _TRAVERSAL.get(relation, BOTH)


def query_intent(query: str) -> tuple:
    """Resolve a query into ``(relation, direction)``.

    ``relation == relates_to`` means "no specific relation" -> a GENERAL query (the store traverses every
    edge both ways, i.e. pre-typed behavior). A specific relation narrows traversal to that relation's
    edges; the direction is the relation's default unless an IN-signal ("dependents", "impact", "used by",
    "what breaks", …) reverses a forward relation to IN.
    """
    q = _normalize(query)  # flatten backticks/quotes/dots so code identifiers parse like plain words
    rel = classify_relation(q)
    if rel == RELATES_TO:
        return (RELATES_TO, BOTH)
    base = edge_direction(rel)
    if base != OUT:
        return (rel, base)  # symmetric relations: direction is moot
    if _SUBJECT_FORM.search(q):   # subject form ("what is X depending on") -> forward; beats bare gerund
        return (rel, OUT)
    if _IN_SIGNAL.search(q):      # clearly the verb's object -> reverse
        return (rel, IN)
    if _OUT_SIGNAL.search(q):     # terse forward noun ("X's dependency", "X callee")
        return (rel, OUT)
    return (rel, BOTH)  # ambiguous direction -> recall-safe BOTH (never drops gold; lower precision)


__all__ = [
    "DEPENDS_ON", "CALLS", "USES", "IMPORTS", "CONFLICTS_WITH", "CONTRADICTS", "RENAMES", "IMPACTS",
    "RELATES_TO", "OUT", "IN", "BOTH", "classify_relation", "edge_direction", "query_intent",
]
