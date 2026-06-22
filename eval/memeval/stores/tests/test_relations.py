"""Unit tests for :mod:`memeval.stores.relations` — the shared relation/direction vocabulary. Owner: Brent.

Locks the two things the typed graph store keys off: (1) `classify_relation` maps an anchor/query phrase
to the right closed-enum relation (and defaults to `relates_to`), and (2) `query_intent` resolves a query
into `(relation, direction)`, including the subtle "what depends on X" (dependents -> IN) vs "what does X
depend on" (dependencies -> OUT) distinction. Stdlib only.
"""

from __future__ import annotations

import unittest

from memeval.stores.relations import (
    BOTH,
    CALLS,
    CONFLICTS_WITH,
    DEPENDS_ON,
    IMPACTS,
    IMPORTS,
    IN,
    OUT,
    RELATES_TO,
    USES,
    classify_relation,
    edge_direction,
    query_intent,
)


class ClassifyRelationTests(unittest.TestCase):
    def test_maps_each_relation_phrase(self) -> None:
        cases = {
            "depends on": DEPENDS_ON, "dependency": DEPENDS_ON, "dependents": DEPENDS_ON,
            "conflicts with": CONFLICTS_WITH, "conflict": CONFLICTS_WITH,
            "calls": CALLS, "callee": CALLS, "caller": CALLS,
            "uses": USES, "used by": USES,
            "impacts": IMPACTS, "affects": IMPACTS, "what breaks": IMPACTS,
        }
        for phrase, rel in cases.items():
            self.assertEqual(classify_relation(phrase), rel, f"{phrase!r} -> {rel}")

    def test_defaults_to_relates_to(self) -> None:
        for phrase in ("", "related to", "the chain tail", "some random words", "Apex"):
            self.assertEqual(classify_relation(phrase), RELATES_TO, f"{phrase!r} should default to relates_to")


class EdgeDirectionTests(unittest.TestCase):
    def test_forward_relations_are_out(self) -> None:
        for rel in (DEPENDS_ON, CALLS, USES):
            self.assertEqual(edge_direction(rel), OUT)

    def test_symmetric_relations_are_both(self) -> None:
        for rel in (CONFLICTS_WITH, RELATES_TO):
            self.assertEqual(edge_direction(rel), BOTH)


class QueryIntentTests(unittest.TestCase):
    def test_forward_dependency_is_out(self) -> None:
        # "X's dependency" / "what does X depend on" -> the things X depends on -> OUT
        for q in ("Zephyr dependency", "what does Zephyr depend on"):
            self.assertEqual(query_intent(q), (DEPENDS_ON, OUT), q)

    def test_dependents_are_in(self) -> None:
        # "X's dependents" / "what depends on X" -> X's sources on the depends_on edge -> IN.
        # ("dependents impact" classifies depends_on because the "depend" token matches first.)
        for q in ("Zephyr dependents impact", "what depends on Zephyr"):
            self.assertEqual(query_intent(q), (DEPENDS_ON, IN), q)

    def test_what_breaks_is_impacts_out(self) -> None:
        # "what breaks if X changes" = what X IMPACTS = the OUT side of an impacts edge (X --impacts--> Y),
        # NOT a reverse query. "what breaks" overrides any other verb named in the query.
        self.assertEqual(query_intent("what breaks if Zephyr changes"), (IMPACTS, OUT))
        self.assertEqual(query_intent("what breaks if I rename findActive"), (IMPACTS, OUT))
        self.assertEqual(query_intent("what breaks if AuthService uses SessionCache"), (IMPACTS, OUT))
        self.assertEqual(query_intent("what breaks if Handler calls Worker"), (IMPACTS, OUT))

    def test_terse_forward_subject_verb_is_out(self) -> None:
        # "<entity> depends on / calls / imports / uses" is a forward statement-form -> OUT (not the
        # recall-safe BOTH fallback); the reverse "modules that depend on X" still resolves IN.
        self.assertEqual(query_intent("Zephyr depends on"), (DEPENDS_ON, OUT))
        self.assertEqual(query_intent("Handler calls"), (CALLS, OUT))
        self.assertEqual(query_intent("modules that depend on Zephyr"), (DEPENDS_ON, IN))

    def test_reverse_phrasings_resolve_to_in(self) -> None:
        # Common production reverse forms (the queried entity is the verb's OBJECT) must be IN.
        self.assertEqual(query_intent("modules that depend on PaymentService"), (DEPENDS_ON, IN))
        self.assertEqual(query_intent("services depending on the gateway"), (DEPENDS_ON, IN))
        self.assertEqual(query_intent("which modules import TokenBucket"), (IMPORTS, IN))
        self.assertEqual(query_intent("which modules call Handler"), (CALLS, IN))
        self.assertEqual(query_intent("who uses the config loader"), (USES, IN))
        self.assertEqual(query_intent("what impacts Zephyr"), (IMPACTS, IN))
        self.assertEqual(query_intent("which modules imported TokenBucket"), (IMPORTS, IN))  # past tense
        self.assertEqual(query_intent("what depended on Zephyr"), (DEPENDS_ON, IN))           # past tense

    def test_forward_phrasings_stay_out(self) -> None:
        # The subject forms ("what does/is X <verb>") must NOT be reversed.
        self.assertEqual(query_intent("what does Zephyr depend on"), (DEPENDS_ON, OUT))
        self.assertEqual(query_intent("what is Zephyr depending on"), (DEPENDS_ON, OUT))
        self.assertEqual(query_intent("what are Zephyr's dependencies"), (DEPENDS_ON, OUT))
        self.assertEqual(query_intent("what does Zephyr impact"), (IMPACTS, OUT))
        self.assertEqual(query_intent("what does Handler call"), (CALLS, OUT))

    def test_ambiguous_direction_defaults_to_both(self) -> None:
        # A terse query with no clear forward or reverse marker is recall-safe BOTH (never drops the
        # incoming-edge gold), not a guessed OUT.
        self.assertEqual(query_intent("Zephyr impact"), (IMPACTS, BOTH))

    def test_subject_form_using(self) -> None:
        # "what is X using" / "what did X import" are subject (forward) queries -> OUT.
        self.assertEqual(query_intent("what is AuthService using"), (USES, OUT))
        self.assertEqual(query_intent("what did Zephyr import"), (IMPORTS, OUT))

    def test_who_is_X_calling_vs_who_is_calling_X(self) -> None:
        # The hard pair: entity after the auxiliary = subject (OUT); verb right after the auxiliary = the
        # entity is the object (IN). The aux-then-entity-then-verb guard separates them.
        self.assertEqual(query_intent("who is Handler calling"), (CALLS, OUT))
        self.assertEqual(query_intent("who is calling Handler"), (CALLS, IN))

    def test_code_identifier_delimiters_normalized(self) -> None:
        # Backticked / quoted / dotted code identifiers must parse like their bare-token forms.
        self.assertEqual(query_intent("who is `Handler` calling"), (CALLS, OUT))
        self.assertEqual(query_intent("what does `schema.py` depend on"), (DEPENDS_ON, OUT))
        self.assertEqual(query_intent("what depends on `schema.py`"), (DEPENDS_ON, IN))
        self.assertEqual(query_intent("what did `token_bucket` import"), (IMPORTS, OUT))

    def test_callee_out_caller_in(self) -> None:
        self.assertEqual(query_intent("Hub callee"), (CALLS, OUT))
        self.assertEqual(query_intent("what calls Hub"), (CALLS, IN))

    def test_conflict_is_symmetric(self) -> None:
        self.assertEqual(query_intent("Hub conflict"), (CONFLICTS_WITH, BOTH))

    def test_general_query_is_relates_to_both(self) -> None:
        # No relation verb -> general intent: traverse everything both ways.
        for q in ("Solis related", "Apex chain tail", "anything at all"):
            self.assertEqual(query_intent(q), (RELATES_TO, BOTH), q)


if __name__ == "__main__":
    unittest.main()
