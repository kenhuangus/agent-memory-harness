"""PR3a bake-off harness — score router classifier strategies on the grown routing eval.

Owner: Brent. Pure stdlib, no installs, no API key, CI-safe.

This is the STRATEGY BAKE-OFF that PR3's heavy classifiers (spaCy, semantic-router, a
learned model) plug into later with NO harness change. It runs each AVAILABLE strategy,
behind the internal :class:`RouterClassifier` seam, over the durable routing eval
(``BLIND_CASES`` hard cases + ``D018_CASES`` — IMPORTED from ``test_routing_evals``,
never duplicated), scores per-bucket agreement, and applies the eval-first acceptance
bar (PR3 architect §5) as a PURE function. Absent strategies report a clean ``SKIP``
with an install / encoder reason, so the real adapters drop in behind the same factory.

The strategy registry is a list of ``(name, factory)`` entries; each factory returns
either a :class:`RouterClassifier` OR a :class:`Skip` sentinel carrying its reason:

* ``rules``           — ``RuleBasedClassifier(RouterConfig())``: today's router,
                        byte-for-byte. Always available — THE dynamic baseline.
* ``fake``            — a trivial always-``markdown`` classifier. Proves the harness
                        scores a non-rules strategy AND that the eligibility bar
                        REJECTS a bad challenger (always-markdown regresses every
                        graph/vector case → not eligible).
* ``spacy`` /
  ``semantic-router`` — ``importlib``-PROBE their deps (``importlib.util.find_spec``,
                        never a hard import) and ``SKIP`` (install extra / encoder not
                        configured). Exercising the skip mechanics NOW means the
                        adapters land later with no harness edit.

Pure stdlib by construction: nothing here can pull spaCy / semantic-router / numpy / a
real embedder into the zero-dependency smoke gate — the optional strategies are only
ever PROBED, never imported.

Dual-mode (both from ``eval/``):
    python3 -m memeval.stores.tests.test_routing_bakeoff      # prints the matrix
    python3 -m unittest memeval.stores.tests.test_routing_bakeoff

What the tests assert (invariants, NOT brittle exact numbers): the harness runs
end-to-end; the ``rules`` row reproduces the locked baseline — cross-checked against
``test_routing_evals.score()``, NOT a hardcoded ``28/31`` — and is NOT eligible vs
itself (delta 0); the ``fake`` strategy is scored and judged NOT eligible by the bar;
the ``SKIP`` rows report cleanly with reasons; and the eligibility function is correct
on crafted synthetic inputs (a beats-rules result → eligible; a result that regresses a
single rule-correct BLIND-hard case → not eligible).
"""

from __future__ import annotations

import importlib.util
import unittest
from dataclasses import dataclass
from typing import Callable, Union

from memeval.router import (
    GRAPH,
    MARKDOWN,
    VECTORS,
    ClassificationResult,
    Router,
    RouterClassifier,
    RouterConfig,
    RuleBasedClassifier,
)

# Import the durable fixture + the locked dynamic scorer (do NOT duplicate them) so the
# numbers are computed exactly the way test_routing_evals computes them.
from memeval.stores.tests.test_routing_evals import (
    BLIND_CASES,
    D018_CASES,
    score as _blind_score,
)

# --------------------------------------------------------------------------- #
# The scored pools, sliced once from the imported fixture.
#   * BLIND-hard       — kind == "hard" (the only graded blind cases).
#   * D018 AGREE       — bucket == "AGREE" (rules already agrees today).
#   * D018 golden      — tier == "golden" (the 100% lock; a subset of AGREE).
#   * GAP:needs-learning — multilingual relation queries English rules miss (recovery).
#   * CONTESTED        — provisional labels; MEASURED-ONLY, excluded from pass/fail.
# Both fixtures share the (query, expected, ...) prefix, so c[0]/c[1] read uniformly.
# --------------------------------------------------------------------------- #
BLIND_HARD = [c for c in BLIND_CASES if c[3] == "hard"]
AGREE_CASES = [c for c in D018_CASES if c[3] == "AGREE"]
GOLDEN_CASES = [c for c in D018_CASES if c[2] == "golden"]
GAP_CASES = [c for c in D018_CASES if c[3] == "GAP:needs-learning"]
CONTESTED_CASES = [c for c in D018_CASES if c[3] == "CONTESTED"]


# --------------------------------------------------------------------------- #
# Strategy seam helpers — the fake classifier + the SKIP sentinel.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Skip:
    """The 'strategy unavailable' sentinel a registry factory returns INSTEAD of a
    :class:`RouterClassifier`, carrying the human-readable ``reason`` (install extra /
    encoder not configured) the matrix prints in that row."""

    reason: str


class FakeMarkdownClassifier:
    """A trivial deterministic strategy that always routes to ``markdown``.

    Exists to prove two harness properties without any dependency: (a) a NON-rules
    strategy can be scored, and (b) the eligibility bar correctly REJECTS a bad
    challenger — always-markdown regresses every rule-correct graph/vector case, so it
    must come back NOT eligible. Satisfies the :class:`RouterClassifier` seam
    structurally (``name`` + ``classify``); no subclassing.
    """

    name = "fake-always-markdown"

    def classify(self, query: str) -> ClassificationResult:
        scores = {GRAPH: 0.0, VECTORS: 0.0, MARKDOWN: 1.0}
        return ClassificationResult(choice=MARKDOWN, scores=scores, margin=1.0)


# --------------------------------------------------------------------------- #
# Strategy registry — list of (name, factory); each factory returns a
# RouterClassifier OR a Skip. Heavy strategies PROBE (never import) their deps.
# --------------------------------------------------------------------------- #
def _rules_factory() -> RouterClassifier:
    """The dynamic baseline: today's router, byte-for-byte. No optional dependency."""
    return RuleBasedClassifier(RouterConfig())


def _fake_factory() -> RouterClassifier:
    """A non-rules strategy that always routes to markdown (the bad challenger)."""
    return FakeMarkdownClassifier()


def _spacy_factory() -> Union[RouterClassifier, Skip]:
    """PROBE for spaCy (find_spec never imports it → CI-safe) and SKIP cleanly.

    spaCy + a trained pipeline is an optional extra; absent here, so SKIP with the
    install reason. The real ``SpacyClassifier`` drops in behind this same factory.
    """
    if importlib.util.find_spec("spacy") is None:
        return Skip("install routing-spacy extra (spaCy + a model, e.g. en_core_web_sm)")
    return Skip("spaCy installed, but the SpacyClassifier adapter ships in a later PR3 step")


def _semantic_router_factory() -> Union[RouterClassifier, Skip]:
    """PROBE for semantic-router and SKIP cleanly (encoder not configured).

    Even with the package installed, no encoder is wired in PR3a (encoder wiring is the
    key-gated PR3b), so this stays a SKIP — exercising the skip mechanics now.
    """
    if importlib.util.find_spec("semantic_router") is None:
        return Skip("install routing-semantic extra (semantic-router) and configure an encoder")
    return Skip("encoder not configured")


REGISTRY: tuple[tuple[str, Callable[[], Union[RouterClassifier, Skip]]], ...] = (
    ("rules", _rules_factory),
    ("fake", _fake_factory),
    ("spacy", _spacy_factory),
    ("semantic-router", _semantic_router_factory),
)


# --------------------------------------------------------------------------- #
# Scoring — per-bucket agreement for one strategy behind the classifier seam.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StrategyScore:
    """Per-bucket correctness for one strategy, as index-aligned tuples of bools.

    Index alignment matters: the eligibility bar's BLIND-hard check is PER-CASE
    (preserve every case the baseline got right), so it zips the baseline and challenger
    ``blind_hard`` tuples — same fixture order, so same index.
    """

    name: str
    blind_hard: tuple
    agree: tuple
    golden: tuple
    gap: tuple
    contested: tuple


def score_strategy(name: str, classifier: RouterClassifier) -> StrategyScore:
    """Route every case through ``classifier`` (behind the seam) and record agreement.

    Uses ``Router.with_config(config=RouterConfig(classifier=...))`` so the strategy is
    exercised through the SAME insertion point PR3's adapters use — not called directly.
    """
    router = Router.with_config(config=RouterConfig(classifier=classifier))

    def hits(cases: list) -> tuple:
        return tuple(router.classify(q) == expected for q, expected, *_ in cases)

    return StrategyScore(
        name=name,
        blind_hard=hits(BLIND_HARD),
        agree=hits(AGREE_CASES),
        golden=hits(GOLDEN_CASES),
        gap=hits(GAP_CASES),
        contested=hits(CONTESTED_CASES),
    )


# --------------------------------------------------------------------------- #
# Eligibility — the eval-first acceptance bar (PR3 architect §5), a PURE function.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Verdict:
    """A challenger's eligibility vs the dynamic rules baseline, with the per-criterion
    breakdown (so the matrix can show WHY)."""

    eligible: bool
    preserves_blind: bool
    preserves_agree: bool
    preserves_golden: bool
    recovers_gap: bool
    meets_delta: bool
    delta: int
    blind_regressions: int
    reasons: tuple


def eligibility(baseline: StrategyScore, challenger: StrategyScore) -> Verdict:
    """Return the challenger's eligibility verdict vs the ``baseline`` (rules).

    Eligible iff, vs the dynamic rules baseline, the challenger:
      1. preserves EVERY rule-correct BLIND-hard case (per-case, not just the count);
      2. preserves D018 AGREE (no drop in the count);
      3. preserves 100% of D018 golden;
      4. recovers >= 2/3 of GAP:needs-learning (absolute bar — baseline recovers 0/3);
      5. nets a non-contested delta >= +2 (non-contested = BLIND-hard + AGREE + GAP;
         golden is a SUBSET of AGREE, so it is not double-counted).
    CONTESTED is excluded entirely. Integer math throughout (no float thresholds).
    """
    # 1. preserve every rule-correct BLIND-hard case (per-case).
    blind_regressions = sum(
        1 for b, c in zip(baseline.blind_hard, challenger.blind_hard) if b and not c
    )
    preserves_blind = blind_regressions == 0
    # 2. preserve D018 AGREE — no drop.
    preserves_agree = sum(challenger.agree) >= sum(baseline.agree)
    # 3. preserve 100% of D018 golden.
    preserves_golden = sum(challenger.golden) == len(challenger.golden)
    # 4. recover >= 2/3 of GAP:needs-learning  (gap_recovered/gap_total >= 2/3).
    gap_total = len(challenger.gap)
    gap_recovered = sum(challenger.gap)
    recovers_gap = gap_recovered * 3 >= 2 * gap_total
    # 5. net non-contested delta >= +2.
    base_nc = sum(baseline.blind_hard) + sum(baseline.agree) + sum(baseline.gap)
    chal_nc = sum(challenger.blind_hard) + sum(challenger.agree) + sum(challenger.gap)
    delta = chal_nc - base_nc
    meets_delta = delta >= 2

    eligible = (
        preserves_blind and preserves_agree and preserves_golden
        and recovers_gap and meets_delta
    )
    reasons = (
        f"preserve rule-correct BLIND-hard: "
        f"{'PASS' if preserves_blind else f'FAIL ({blind_regressions} regressed)'}",
        f"preserve D018 AGREE (no drop): {'PASS' if preserves_agree else 'FAIL'} "
        f"({sum(challenger.agree)} vs {sum(baseline.agree)})",
        f"preserve 100% D018 golden: {'PASS' if preserves_golden else 'FAIL'} "
        f"({sum(challenger.golden)}/{len(challenger.golden)})",
        f"recover >=2/3 GAP:needs-learning: {'PASS' if recovers_gap else 'FAIL'} "
        f"({gap_recovered}/{gap_total})",
        f"net non-contested delta >= +2: {'PASS' if meets_delta else 'FAIL'} ({delta:+d})",
    )
    return Verdict(
        eligible=eligible,
        preserves_blind=preserves_blind,
        preserves_agree=preserves_agree,
        preserves_golden=preserves_golden,
        recovers_gap=recovers_gap,
        meets_delta=meets_delta,
        delta=delta,
        blind_regressions=blind_regressions,
        reasons=reasons,
    )


# --------------------------------------------------------------------------- #
# Run the whole bake-off — score available strategies, record skips, judge.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BakeoffResult:
    """The full bake-off: registry order, scored strategies, skips, verdicts, baseline."""

    order: tuple
    scores: dict
    skips: dict
    verdicts: dict
    baseline: StrategyScore


def run_bakeoff() -> BakeoffResult:
    """Drive every registry entry: score the AVAILABLE strategies, record the SKIPs,
    then judge each scored strategy against the dynamic ``rules`` baseline."""
    scores: dict = {}
    skips: dict = {}
    for name, factory in REGISTRY:
        produced = factory()
        if isinstance(produced, Skip):
            skips[name] = produced.reason
        elif isinstance(produced, RouterClassifier):  # structural seam check
            scores[name] = score_strategy(name, produced)
        else:
            # A factory returning neither a classifier nor a Skip is itself a bad
            # strategy — reject it as a SKIP rather than crash the matrix.
            skips[name] = f"factory returned {type(produced).__name__}, not a RouterClassifier"

    baseline = scores["rules"]  # rules is always available — the dynamic baseline.
    verdicts = {name: eligibility(baseline, s) for name, s in scores.items()}
    return BakeoffResult(
        order=tuple(name for name, _ in REGISTRY),
        scores=scores,
        skips=skips,
        verdicts=verdicts,
        baseline=baseline,
    )


# --------------------------------------------------------------------------- #
# Report — the printed matrix (rows = strategies, cols = per-bucket + eligibility).
# --------------------------------------------------------------------------- #
def _cell(correct: tuple) -> str:
    return f"{sum(correct)}/{len(correct)}"


def _report() -> None:
    result = run_bakeoff()
    print("ROUTER CLASSIFIER BAKE-OFF — strategies vs the dynamic rules baseline")
    print(
        f"Eval pool (imported from test_routing_evals): BLIND-hard={len(BLIND_HARD)}  "
        f"D018 AGREE={len(AGREE_CASES)}  golden={len(GOLDEN_CASES)}  "
        f"GAP:needs-learning={len(GAP_CASES)}  CONTESTED={len(CONTESTED_CASES)} (report-only)."
    )
    print(
        "Eligibility bar (vs rules): preserve every rule-correct BLIND-hard, no AGREE drop,\n"
        "  100% golden, recover >=2/3 GAP, net non-contested delta >= +2. CONTESTED excluded.\n"
    )

    hdr = (
        f"{'strategy':<18} {'BLIND-hard':<11} {'AGREE':<8} {'golden':<8} "
        f"{'GAP-recov':<10} {'CONTESTED*':<11} {'delta':<7} {'eligible':<8}"
    )
    print(hdr)
    print("-" * len(hdr))
    for name in result.order:
        if name in result.skips:
            print(f"{name:<18} SKIP — {result.skips[name]}")
            continue
        s = result.scores[name]
        v = result.verdicts[name]
        print(
            f"{name:<18} {_cell(s.blind_hard):<11} {_cell(s.agree):<8} "
            f"{_cell(s.golden):<8} {_cell(s.gap):<10} {_cell(s.contested):<11} "
            f"{v.delta:<+7d} {('yes' if v.eligible else 'no'):<8}"
        )

    print("\n* CONTESTED is measured-only (provisional labels) — excluded from pass/fail.\n")
    print("Eligibility detail (vs the dynamic rules baseline):")
    for name in result.order:
        if name in result.skips:
            print(f"  {name}: SKIP — {result.skips[name]}")
            continue
        v = result.verdicts[name]
        print(f"  {name}: {'ELIGIBLE' if v.eligible else 'NOT eligible'}")
        for reason in v.reasons:
            print(f"      - {reason}")


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def _bits(n_true: int, total: int) -> tuple:
    """A correctness tuple: ``n_true`` Trues followed by the remaining Falses."""
    return tuple([True] * n_true + [False] * (total - n_true))


class EligibilityFunctionTests(unittest.TestCase):
    """The eval-first bar is a PURE function — proven on crafted synthetic inputs,
    independent of the real fixture (architect §5)."""

    def _baseline(self) -> StrategyScore:
        # A stand-in for the rules baseline: 28/31 BLIND-hard, 24/24 AGREE, 12/12
        # golden, 0/3 GAP. Counts only — the per-case BLIND check zips by index.
        return StrategyScore(
            "base", _bits(28, 31), _bits(24, 24), _bits(12, 12), _bits(0, 3), _bits(8, 17)
        )

    def test_beats_rules_is_eligible(self) -> None:
        # Preserves all 28 rule-correct BLIND-hard (same prefix), holds AGREE/golden,
        # recovers all 3 GAP -> net delta +3 -> ELIGIBLE.
        chal = StrategyScore(
            "beats", _bits(28, 31), _bits(24, 24), _bits(12, 12), _bits(3, 3), _bits(8, 17)
        )
        v = eligibility(self._baseline(), chal)
        self.assertTrue(v.eligible, v.reasons)
        self.assertGreaterEqual(v.delta, 2)

    def test_regressing_one_blind_hard_is_not_eligible(self) -> None:
        # Flip index 0 (a rule-correct BLIND-hard) to False but still recover all GAP,
        # so the delta bar (+2) is met — the PER-CASE BLIND constraint is what fails.
        chal = StrategyScore(
            "regress",
            (False,) + tuple([True] * 27 + [False] * 3),  # 27/31; index 0 regressed
            _bits(24, 24),
            _bits(12, 12),
            _bits(3, 3),
            _bits(8, 17),
        )
        v = eligibility(self._baseline(), chal)
        self.assertFalse(v.eligible)
        self.assertFalse(v.preserves_blind)
        self.assertEqual(v.blind_regressions, 1)
        self.assertTrue(v.meets_delta, "this synthetic isolates the per-case BLIND check")

    def test_missing_gap_recovery_is_not_eligible(self) -> None:
        # Preserves everything but recovers 0/3 GAP -> fails the GAP bar (and the delta).
        chal = StrategyScore(
            "nogap", _bits(28, 31), _bits(24, 24), _bits(12, 12), _bits(0, 3), _bits(8, 17)
        )
        v = eligibility(self._baseline(), chal)
        self.assertFalse(v.eligible)
        self.assertFalse(v.recovers_gap)

    def test_dropping_agree_is_not_eligible(self) -> None:
        # Recovers GAP (delta still +2) but drops one AGREE -> the no-drop bar fails.
        chal = StrategyScore(
            "agredrop", _bits(28, 31), _bits(23, 24), _bits(12, 12), _bits(3, 3), _bits(8, 17)
        )
        v = eligibility(self._baseline(), chal)
        self.assertFalse(v.eligible)
        self.assertFalse(v.preserves_agree)


class BakeoffHarnessTests(unittest.TestCase):
    """The harness runs end-to-end and judges the real ``rules`` + ``fake`` rows,
    with the ``spacy`` / ``semantic-router`` rows SKIPping cleanly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_bakeoff()

    def test_harness_runs_end_to_end(self) -> None:
        # rules + fake scored; spacy + semantic-router skipped — all four accounted for.
        self.assertIn("rules", self.result.scores)
        self.assertIn("fake", self.result.scores)
        self.assertEqual(
            set(self.result.order), {"rules", "fake", "spacy", "semantic-router"}
        )
        self.assertEqual(
            set(self.result.scores) | set(self.result.skips), set(self.result.order)
        )

    def test_rules_row_reproduces_locked_baseline_dynamically(self) -> None:
        # NOT hardcoded: cross-check the BLIND-hard count against test_routing_evals'
        # locked dynamic scorer, and AGREE/golden against the bucket invariants.
        s = self.result.scores["rules"]
        blind_agree, blind_total, _ = _blind_score()
        self.assertEqual(sum(s.blind_hard), blind_agree)
        self.assertEqual(len(s.blind_hard), blind_total)
        # "AGREE" bucket == rules already agrees; "golden" is the 100% lock.
        self.assertEqual(sum(s.agree), len(s.agree), "rules must agree with all AGREE cases")
        self.assertEqual(sum(s.golden), len(s.golden), "rules must agree with all golden cases")
        # The multilingual GAP is exactly what rules CANNOT do (that is PR3's job).
        self.assertEqual(sum(s.gap), 0)

    def test_rules_not_eligible_vs_itself(self) -> None:
        # A strategy compared to itself nets a 0 delta and recovers no GAP -> not eligible.
        v = self.result.verdicts["rules"]
        self.assertFalse(v.eligible)
        self.assertEqual(v.delta, 0)
        self.assertFalse(v.recovers_gap)

    def test_fake_is_scored_and_judged_not_eligible(self) -> None:
        # The bar must REJECT a bad challenger: always-markdown is scored (a non-rules
        # strategy ran) yet regresses rule-correct graph/vector BLIND-hard cases.
        self.assertIn("fake", self.result.scores)
        v = self.result.verdicts["fake"]
        self.assertFalse(v.eligible)
        self.assertFalse(v.preserves_blind)
        self.assertGreater(v.blind_regressions, 0)

    def test_skips_report_cleanly_with_reasons(self) -> None:
        for name in ("spacy", "semantic-router"):
            self.assertIn(name, self.result.skips)
            self.assertTrue(self.result.skips[name].strip(), f"{name} SKIP needs a reason")
            self.assertNotIn(name, self.result.scores)

    def test_pure_stdlib_no_optional_strategy_imports(self) -> None:
        # The bake-off must never pull its optional strategies into the process — the
        # factories PROBE, never import. (Guards the zero-dep smoke gate.)
        import sys

        for mod in ("spacy", "semantic_router", "numpy"):
            self.assertNotIn(
                mod, sys.modules, f"{mod} was imported — bake-off must stay pure stdlib / CI-safe"
            )


if __name__ == "__main__":
    _report()
