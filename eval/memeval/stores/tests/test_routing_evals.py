"""Durable blind routing eval — the 41 adversarial queries from the blind hardening + adjudication rounds.

These were authored by 4 subagents *firewalled from router.py* (4 lenses: surface
traps, genuine ambiguity, messy/real phrasing, boundary inputs). This file makes the
58% -> 83% -> 90% progression reproducible from the repo rather than a throwaway script.

Reproduce the report:   cd eval && python3 -m memeval.stores.tests.test_routing_evals
Run the regression guard: cd eval && python3 -m unittest memeval.stores.tests.test_routing_evals

`expected` is the *blind generator's* label. Where the team later adjudicated a
different route (a team adjudication) or accepted a known limitation, `note` records it — those
disagreements are INTENTIONAL, not regressions. Only `kind == "hard"` cases are graded.

D018 growth set (see `D018_CASES` below): 42 further blind-generated cases, bucketed
AGREE / GAP / CONTESTED by a synth round (`work/agents/d018-synth/verifier/output.md`).
They live in a SEPARATE pool so the BLIND_CASES floor and `_EXPECTED_HARD` lock are
untouched. Only D018 `golden` cases (AGREE, unambiguous, currently agreeing) are
hard-asserted; every GAP + contested case is MEASURED ONLY (printed, never asserted),
because they intentionally encode current mis-routes / provisional labels for the
follow-up router work to act on.
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

# === D018: routing-eval growth — 42 new blind cases, folded 2026-06 ===
# Source: work/agents/d018-synth/verifier/output.md (merged, deduped, bucketed).
# `expected` is the synth's label (provisional for CONTESTED). `tier` controls grading:
#   golden       -> HARD-ASSERTED (must agree 100%); only AGREE-bucket, unambiguous cases.
#   edge / adv.  -> MEASURED ONLY (printed, never asserted) — these carry the current
#                   mis-routes (GAP) and provisional/contested labels on purpose.
# `note` carries the follow-up tag so the router PRs can grep these out:
#   "D018 GAP cheap-fix (router rule)"  -> add a narrow router rule (PR follow-up)
#   "D018 known-limit: multilingual → PR3 learned classifier"
#   "⚠ D018 contested (provisional label)"
#
# (query, expected, tier, bucket, note)
#   `tier` is the FIXTURE GRADING tier (golden=hard-asserted must-pass | edge/adversarial=measured),
#   re-assigned for grading here; it is NOT a verbatim copy of the synth report's lens-tier.
D018_CASES = [
    # -- AGREE (13): classify_now == expected today. Unambiguous single-intent -> golden;
    #    messier / surface-trap / boundary AGREE cases -> edge. All MEASURED; golden also asserted.
    ("Why'd we go with `lru_cache` here instead of hand-rolling a cache?", "vectors", "edge", "AGREE", "D018 AGREE"),
    ('Which modules call the function that logs "backend unavailable"?', "graph", "golden", "AGREE", "D018 AGREE"),
    ("What's the `depends_on` value listed for the D018 feature?", "markdown", "golden", "AGREE", "D018 AGREE"),
    ("ugh got `KeyError: 'profile'` again — wait what was that field actually called in the config, profile or profiles?", "markdown", "edge", "AGREE", "D018 AGREE"),
    ("so the token refresh — i think it calls into the session store but honestly idk if the rate limiter depends on it too or the other way round", "graph", "edge", "AGREE", "D018 AGREE"),
    ("remind me the whole reasoning for why we went with sqlite instead of postgres for the store", "vectors", "golden", "AGREE", "D018 AGREE"),
    ("keep seeing `pool_timeout=30` in the logs — is 30 what we set or just the default?", "markdown", "edge", "AGREE", "D018 AGREE"),
    ("WHAT BREAKS IF AUTH_SERVICE USES SESSION_CACHE", "graph", "golden", "AGREE", "D018 AGREE"),
    ('Traceback (most recent call last):\n  File "eval/memeval/stores/loader.py", line 88, in load_cases\nKeyError: \'expected_backend\'', "markdown", "edge", "AGREE", "D018 AGREE"),
    ("USE_GRAPH_CASCADE のデフォルト値は？", "markdown", "golden", "AGREE", "D018 AGREE"),
    ("Покажи точную строку с текстом «rate limit exceeded»", "markdown", "edge", "AGREE", "D018 AGREE"),
    # multilingual rationale: route right only via the semantic DEFAULT (zero-signal fallthrough),
    # not genuine multilingual recognition -> measured edge, NOT hard-asserted (multilingual is the D018/PR3 frontier).
    ("¿Por qué elegimos un almacén vectorial en lugar de un grafo para el recuerdo semántico?", "vectors", "edge", "AGREE", "D018 AGREE"),
    ("なぜ再ランク付けを無効にしたのか、その背景を説明して。", "vectors", "edge", "AGREE", "D018 AGREE"),
    # -- GAP: cheap-fix (9): MIS-ROUTE today; fixable by a narrow router rule. MEASURED ONLY. --
    ("What's the value of keeping a write-ahead log on the store?", "vectors", "adversarial", "GAP:cheap-fix", "D018 GAP cheap-fix (router rule)"),
    ("Remind me what we ended up calling the flag that disables dreaming during eval runs.", "markdown", "edge", "GAP:cheap-fix", "D018 GAP cheap-fix (router rule)"),
    ("Give me the gist of what `RouterConfig` is actually for.", "vectors", "adversarial", "GAP:cheap-fix", "D018 GAP cheap-fix (router rule)"),
    ("What else touches `RouterConfig` besides the graph→vector cascade?", "graph", "adversarial", "GAP:cheap-fix", "D018 GAP cheap-fix (router rule)"),
    ("whats teh exact naem of teh env var for the anthropic key? sumthing like ANTHROPIC_...", "markdown", "edge", "GAP:cheap-fix", "D018 GAP cheap-fix (router rule)"),
    ("ok dumb q — if i bump the embedding model version does anything downstream actually break, like does the reranker or the cache care?", "graph", "edge", "GAP:cheap-fix", "D018 GAP cheap-fix (router rule)"),
    ("hmm i swear there was a comment somewhere that literally said 'do not call this inside a loop' — where was that again", "markdown", "adversarial", "GAP:cheap-fix", "D018 GAP cheap-fix (router rule)"),
    ("https://docs.example.internal/router/routing-evals#zero-token-default", "markdown", "adversarial", "GAP:cheap-fix", "D018 GAP cheap-fix (router rule)"),
    ("why did we decide the memory router should fall back to semantic retrieval when a request is vague, noisy, missing obvious identifiers, missing exact file paths, missing quoted strings, and missing any clear pair of related components? please summarize the rationale and tradeoffs from memory.", "vectors", "adversarial", "GAP:cheap-fix", "D018 GAP cheap-fix (router rule)"),
    # -- GAP: needs-learning (3): multilingual relation queries; English graph rules miss them. MEASURED ONLY. --
    ("Wovon hängt der `RouterConfig`-Loader ab?", "graph", "adversarial", "GAP:needs-learning", "D018 known-limit: multilingual → PR3 learned classifier"),
    ("哪些模块依赖 embedding_store？", "graph", "adversarial", "GAP:needs-learning", "D018 known-limit: multilingual → PR3 learned classifier"),
    ("Le module `cache` entre-t-il en conflit avec `rate_limiter` ?", "graph", "adversarial", "GAP:needs-learning", "D018 known-limit: multilingual → PR3 learned classifier"),
    # -- CONTESTED (17): provisional synth label; team has not adjudicated. MEASURED ONLY, marked ⚠. --
    ("What does `idempotency_key` actually refer to in our setup?", "vectors", "edge", "CONTESTED", "⚠ D018 contested (provisional label)"),
    ("compare `auth_retry_budget` with the retry policy we rejected", "vectors", "edge", "CONTESTED", "⚠ D018 contested (provisional label)"),
    ("what changed when we swapped `MemoryIndex` for `GraphIndex`?", "graph", "edge", "CONTESTED", "⚠ D018 contested (provisional label)"),
    ("give me the context around `/docs/router-notes.md`", "markdown", "edge", "CONTESTED", "⚠ D018 contested (provisional label)"),
    ("why did `cache_key_v2` break the importer?", "graph", "edge", "CONTESTED", "⚠ D018 contested (provisional label)"),
    ("notes on `agent-roster` and `agent-memory-harness` together", "graph", "edge", "CONTESTED", "⚠ D018 contested (provisional label)"),
    ("find the decision about using SQLite instead of JSONL", "vectors", "edge", "CONTESTED", "⚠ D018 contested (provisional label)"),
    ("what did we say about `router.py` touching scoring?", "markdown", "edge", "CONTESTED", "⚠ D018 contested (provisional label)"),
    ("summarize the link between `ingest_memories` and duplicate detection", "graph", "edge", "CONTESTED", "⚠ D018 contested (provisional label)"),
    ("the one where `ThreadStore` conflicted with persistence cleanup", "graph", "edge", "CONTESTED", "⚠ D018 contested (provisional label)"),
    ("wait where did we actually land on the retry backoff thing in the end", "vectors", "edge", "CONTESTED", "⚠ D018 contested (provisional label)"),
    ("does `RouterConfig` even use the `profile` field anymore or is that dead code now", "graph", "edge", "CONTESTED", "⚠ D018 contested (provisional label)"),
    ("x", "markdown", "edge", "CONTESTED", "⚠ D018 contested (provisional label)"),
    ("404", "vectors", "edge", "CONTESTED", "⚠ D018 contested (provisional label)"),
    ('Why does `route_query("call graph for MemoryIndex")` keep returning `DEFAULT_BACKEND` instead of graph?', "vectors", "edge", "CONTESTED", "⚠ D018 contested (provisional label)"),
    ("Wie lautet der genaue Name der Umgebungsvariable für den API-Schlüssel?", "markdown", "edge", "CONTESTED", "⚠ D018 contested (provisional label)"),
    ("Explique pourquoi `exponential_backoff` est préférable au `fixed_delay` ici.", "vectors", "edge", "CONTESTED", "⚠ D018 contested (provisional label)"),
]

_EXPECTED_D018 = 42  # locks the D018 denominator — adding/removing a case must be deliberate
_D018_TIERS = {"golden", "edge", "adversarial"}
_D018_BUCKETS = {"AGREE", "GAP:cheap-fix", "GAP:needs-learning", "CONTESTED"}
_D018_LABELS = {"graph", "vectors", "markdown"}


def score():
    """Return (agree, total_hard, misses) of router agreement with the blind labels."""
    r = Router()
    hard = [c for c in BLIND_CASES if c[3] == "hard"]
    misses = [(c, r.classify(c[0])) for c in hard if r.classify(c[0]) != c[1]]
    return len(hard) - len(misses), len(hard), misses


def score_d018():
    """Return [(case, got), ...] of router classification over every D018 case (all measured)."""
    r = Router()
    return [(c, r.classify(c[0])) for c in D018_CASES]


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


class D018RoutingEvalTests(unittest.TestCase):
    """D018 growth pool. The hard guarantee here is narrow on purpose: only `golden`
    (AGREE, unambiguous, currently-agreeing) cases are asserted. GAP + contested cases
    are MEASURED ONLY — they encode current mis-routes / provisional labels, so asserting
    on them would just bake today's gaps into the regression guard."""

    def test_d018_fixture_contract_is_valid(self) -> None:
        seen = {c[0] for c in BLIND_CASES}  # also guard against collisions with the existing pool
        for i, case in enumerate(D018_CASES):
            self.assertEqual(len(case), 5, f"D018 case[{i}] must be a 5-tuple")
            query, expected, tier, bucket, note = case
            self.assertIn(tier, _D018_TIERS, f"D018 case[{i}] bad tier: {tier!r}")
            self.assertIn(expected, _D018_LABELS, f"D018 case[{i}] bad expected: {expected!r}")
            self.assertIn(bucket, _D018_BUCKETS, f"D018 case[{i}] bad bucket: {bucket!r}")
            self.assertNotIn(query, seen, f"D018 case[{i}] duplicate query")
            seen.add(query)
            # GAP / contested cases must stay measured-only (never golden) and carry a follow-up tag.
            if bucket != "AGREE":
                self.assertNotEqual(tier, "golden",
                                    f"D018 case[{i}] {bucket} must not be golden (measured-only)")
                self.assertTrue(note.strip(), f"D018 case[{i}] {bucket} must carry a follow-up note tag")
        self.assertEqual(len(D018_CASES), _EXPECTED_D018,
                         "D018 case count changed — update _EXPECTED_D018 deliberately")

    def test_d018_golden_cases_agree_100pct(self) -> None:
        # golden = AGREE-bucket cases the router already classifies correctly; these are the
        # only D018 cases we assert. If a golden case mis-routes, demote it to edge — do not
        # weaken this assertion.
        r = Router()
        golden = [c for c in D018_CASES if c[2] == "golden"]
        self.assertGreater(len(golden), 0, "no D018 golden cases — golden assertion is vacuous")
        misroutes = [(c[0][:50], c[1], r.classify(c[0])) for c in golden if r.classify(c[0]) != c[1]]
        self.assertEqual(misroutes, [], f"D018 golden cases must agree 100%: {misroutes}")

    def test_d018_golden_are_all_agree_bucket(self) -> None:
        # structural guard: nothing from a GAP / contested bucket can sneak into the asserted pool.
        non_agree_golden = [c[0][:50] for c in D018_CASES if c[2] == "golden" and c[3] != "AGREE"]
        self.assertEqual(non_agree_golden, [],
                         f"golden tier is reserved for AGREE bucket; offenders: {non_agree_golden}")


def _report() -> None:
    agree, total, misses = score()
    print(f"Blind adversarial set: {len(BLIND_CASES)} queries ({total} hard-graded).")
    print(f"Router agreement with blind labels (hard cases): {agree}/{total} = {round(100 * agree / total)}%")
    if misses:
        print("Disagreements (all should be intentional — adjudicated or known limits):")
        for c, got in misses:
            print(f"  blind={c[1]:8} got={got:8} | {c[4].strip() or '*** UNDOCUMENTED ***'} | {c[0][:55]}")


def _report_d018() -> None:
    rows = score_d018()
    total = len(rows)
    agree = sum(1 for c, got in rows if got == c[1])
    golden = [(c, got) for c, got in rows if c[2] == "golden"]
    golden_agree = sum(1 for c, got in golden if got == c[1])
    print()
    print(f"D018 growth set: {total} new blind cases "
          f"({len(golden)} golden hard-asserted; GAP + contested MEASURED ONLY).")
    print(f"Router agreement with D018 provisional labels (ALL measured): "
          f"{agree}/{total} = {round(100 * agree / total)}%   "
          f"<- lower than the existing pool by design (harder set)")
    print(f"  golden (asserted): {golden_agree}/{len(golden)} = "
          f"{round(100 * golden_agree / len(golden))}%")
    for bucket in ("AGREE", "GAP:cheap-fix", "GAP:needs-learning", "CONTESTED"):
        br = [(c, got) for c, got in rows if c[3] == bucket]
        if not br:
            continue
        a = sum(1 for c, got in br if got == c[1])
        print(f"  [{bucket:18}] {a}/{len(br)} agree")
    print(f"Follow-up backlog committed by this fold: "
          f"GAP cheap-fix=9, GAP needs-learning=3, contested=17.")


if __name__ == "__main__":
    _report()
    _report_d018()
