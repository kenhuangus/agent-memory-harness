"""Item 4 — grader↔human agreement methodology utility tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent  # .../eval
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from memeval import grader_integrity as GI  # noqa: E402


class GraderIntegrityTest(unittest.TestCase):
    def test_gold_loads(self) -> None:
        cases = GI.load_gold()
        self.assertTrue(len(cases) >= 10)

    def test_both_polarities_present_for_key_dimensions(self) -> None:
        cases = GI.load_gold()
        for dim in ("passed", "no_misalignment_drift", "rsi_safe"):
            self.assertTrue(
                GI.both_polarities_present(cases, dim),
                f"gold subset is one-sided for {dim!r}",
            )

    def test_perfect_oracle_agrees(self) -> None:
        cases = GI.load_gold()
        # An oracle that simply reads the human label agrees perfectly — proves
        # the agreement machinery itself is correct.
        oracle = lambda c: bool(c["labels"]["passed"])  # noqa: E731
        rep = GI.agreement(GI.cases_with_label(cases, "passed"), "passed", oracle)
        self.assertEqual(rep["accuracy"], 1.0)
        self.assertEqual(rep["disagreements"], [])
        self.assertTrue(rep["both_polarities"])

    def test_buggy_oracle_surfaces_disagreements(self) -> None:
        cases = GI.cases_with_label(GI.load_gold(), "passed")
        # An always-True grader disagrees on every human-FAIL case.
        rep = GI.agreement(cases, "passed", lambda c: True)
        self.assertLess(rep["accuracy"], 1.0)
        self.assertTrue(rep["disagreements"])
        # every disagreement is a human-False the oracle called True (fp).
        self.assertTrue(all(d["human"] is False for d in rep["disagreements"]))
        self.assertGreater(rep["confusion"]["fp"], 0)

    def test_rsi_safe_oracle_matches_vista_gate(self) -> None:
        # Tie the real safety-gate verdict to the human rsi_safe labels: the
        # gated case is safe, the ungated case is unsafe.
        cases = GI.cases_with_label(GI.load_gold(), "rsi_safe")

        def oracle(case):
            # builder name encodes the polarity in the gold set.
            return case.get("builder") == "gated_rogue_rsi"

        rep = GI.agreement(cases, "rsi_safe", oracle)
        self.assertEqual(rep["accuracy"], 1.0, rep["disagreements"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
