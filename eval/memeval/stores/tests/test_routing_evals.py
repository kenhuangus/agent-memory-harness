"""Durable blind routing eval — the 41 adversarial queries from the blind hardening + adjudication rounds.

These were authored by 4 subagents *firewalled from router.py* (4 lenses: surface
traps, genuine ambiguity, messy/real phrasing, boundary inputs). This file makes the
58% -> 83% -> 90% progression reproducible from the repo rather than a throwaway script.

Reproduce the report:   cd eval && python3 -m memeval.stores.tests.test_routing_evals
Run the regression guard: cd eval && python3 -m unittest memeval.stores.tests.test_routing_evals

`expected` is the *blind generator's* label. Where the team later adjudicated a
different route (a team adjudication) or accepted a known limitation, `note` records it — those
disagreements are INTENTIONAL, not regressions. Only `kind == "hard"` cases are graded.
"""

from __future__ import annotations

import unittest

from memeval.router import Router

_LONG = "recall the caching tradeoff " * 9000  # ~250k-char boundary input

# (query, blind_label, lens, kind, note)   kind: hard | amb | none
BLIND_CASES = [
    # -- Lens 1: surface-form traps --
    ("why is `parse_config` so slow on cold start", "vectors", "surface", "hard", ""),
    ("what's the exact name of the retry-count constant we settled on", "markdown", "surface", "hard", ""),
    ("I really like how the caching notes connect to the rate-limiter idea", "vectors", "surface", "hard", "known limit: topical 'connect to' reads as a graph edge"),
    ("which modules import `TokenBucket`", "graph", "surface", "hard", ""),
    ("summarize the auth flow `validateSession` -> `issueJWT`", "vectors", "surface", "amb", ""),
    ("env var name for the S3 bucket override", "markdown", "surface", "hard", ""),
    ("does the new backoff policy contradict what we decided about idempotency", "graph", "surface", "hard", ""),
    ("where did we explain the rationale behind dropping `Redis` for sessions", "vectors", "surface", "hard", ""),
    ("the line that sets `MAX_RETRIES = 5`", "markdown", "surface", "hard", ""),
    ("how do the embedding store and the reranker depend on each other", "graph", "surface", "hard", ""),
    ("what does `EAGAIN` mean in our retry wrapper", "vectors", "surface", "amb", ""),
    # -- Lens 2: genuine ambiguity / multi-intent --
    ("why does the `PaymentService` depend on the `RetryQueue`?", "graph", "ambig", "hard", ""),
    ("everything we know about the `AuthGuard` middleware", "graph", "ambig", "hard", "adjudicated to vectors"),
    ("compare our chosen retry-backoff strategy to the exponential one we rejected", "vectors", "ambig", "hard", ""),
    ("what breaks if I rename `UserRepository.findActive`?", "graph", "ambig", "hard", ""),
    ("how does our rate-limiting design connect to the idempotency rationale?", "graph", "ambig", "amb", ""),
    ("the reasoning behind using `WAL_MODE=true` in the SQLite config", "vectors", "ambig", "hard", ""),
    ("summarize the modules that import the logging package and why", "graph", "ambig", "hard", ""),
    ("where did we note the tradeoff between `Postgres` and `DynamoDB`?", "vectors", "ambig", "hard", ""),
    ("do any of our caching decisions contradict the stateless-API principle?", "graph", "ambig", "hard", ""),
    ("what's the relationship between the `feature-flags` doc and our rollout philosophy?", "graph", "ambig", "amb", ""),
    # -- Lens 3: messy / real --
    ("ok so way back when we ripped out the token bucket thing on the rate limiter — whatd we land on instead and why not just keep it", "vectors", "messy", "amb", ""),
    ("that retry bug", "markdown", "messy", "hard", ""),
    ("wait what calls `normalizeUserPayload` now that we split it, like is it still the webhook handler or did that move", "graph", "messy", "hard", ""),
    ("the databse migration we revrted last sprint, remind me what it actually touched", "vectors", "messy", "amb", ""),
    ("why'd we decide it wasn't worth it again", "vectors", "messy", "hard", ""),
    ("does anything still import the old auth helper or did the v2 thing replace all of them everywhere", "graph", "messy", "hard", ""),
    ("give me the gist of what we said about caching tradeoffs in that long thread", "vectors", "messy", "hard", ""),
    ("what was that flag called the one we set to true to skip the email step in staging", "markdown", "messy", "hard", ""),
    ("remind me how the queue consumer and the dead letter thing relate, does one feed the other or", "graph", "messy", "hard", ""),
    ("hold on did we ever actually contradict ourselves on whether to log PII — i swear one decision said mask it and another said drop it", "graph", "messy", "hard", ""),
    # -- Lens 4: boundary / degenerate --
    ("", "none", "boundary", "none", ""),
    ("   ", "none", "boundary", "none", ""),
    ("auth", "markdown", "boundary", "hard", ""),
    ("???!!! \U0001f525\U0001f4a5\U0001f916", "none", "boundary", "none", ""),
    ("TypeError: x is not a function\n  at foo (app.js:12)\n  at bar (app.js:3)", "markdown", "boundary", "hard", ""),
    (_LONG, "vectors", "boundary", "hard", "very long query (~250k chars)"),
    ("¿Qué función llama a authenticateUser? 認証はどこで使われる?", "graph", "boundary", "hard", "known limit: non-English relational verbs"),
    ("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0", "markdown", "boundary", "hard", ""),
    ("Why did we pick bcrypt and what calls hashPassword()?", "none", "boundary", "none", ""),
    ("SELECT * FROM users; DROP TABLE memory;--", "markdown", "boundary", "hard", ""),
]

_FLOOR = 0.85  # router must agree with >= 85% of the blind HARD cases
_EXPECTED_HARD = 31  # locks the denominator — adding/removing a hard case must be deliberate
_VALID_KINDS = {"hard", "amb", "none"}
_VALID_LABELS = {"graph", "vectors", "markdown", "none"}


def score():
    """Return (agree, total_hard, misses) of router agreement with the blind labels."""
    r = Router()
    hard = [c for c in BLIND_CASES if c[3] == "hard"]
    misses = [(c, r.classify(c[0])) for c in hard if r.classify(c[0]) != c[1]]
    return len(hard) - len(misses), len(hard), misses


class RoutingEvalTests(unittest.TestCase):
    def test_fixture_contract_is_valid(self) -> None:
        seen: set = set()
        hard = 0
        for i, case in enumerate(BLIND_CASES):
            self.assertEqual(len(case), 5, f"case[{i}] must be a 5-tuple")
            query, label, _lens, kind, _note = case
            self.assertIn(kind, _VALID_KINDS, f"case[{i}] bad kind: {kind!r}")
            self.assertIn(label, _VALID_LABELS, f"case[{i}] bad label: {label!r}")
            self.assertNotIn(query, seen, f"case[{i}] duplicate query")
            seen.add(query)
            if kind == "hard":
                hard += 1
                self.assertIn(label, {"graph", "vectors", "markdown"},
                              f"hard case[{i}] cannot have label {label!r}")
        self.assertEqual(hard, _EXPECTED_HARD,
                         "hard-case count changed — update _EXPECTED_HARD deliberately")

    def test_blind_hard_case_agreement_meets_floor(self) -> None:
        agree, total, misses = score()
        self.assertGreater(total, 0, "no hard cases configured — floor check is invalid")
        self.assertGreaterEqual(
            agree / total, _FLOOR,
            f"{agree}/{total}; unexpected misses: "
            f"{[(c[0][:40], c[1], got) for c, got in misses if not c[4].strip()]}")

    def test_all_intentional_misses_are_documented(self) -> None:
        # every hard-case disagreement must carry a `note` (adjudication or known limit)
        _, _, misses = score()
        undocumented = [c[0] for c, _ in misses if not c[4].strip()]
        self.assertEqual(undocumented, [], f"undocumented router/blind disagreements: {undocumented}")


def _report() -> None:
    agree, total, misses = score()
    print(f"Blind adversarial set: {len(BLIND_CASES)} queries ({total} hard-graded).")
    print(f"Router agreement with blind labels (hard cases): {agree}/{total} = {round(100 * agree / total)}%")
    if misses:
        print("Disagreements (all should be intentional — adjudicated or known limits):")
        for c, got in misses:
            print(f"  blind={c[1]:8} got={got:8} | {c[4].strip() or '*** UNDOCUMENTED ***'} | {c[0][:55]}")


if __name__ == "__main__":
    _report()
