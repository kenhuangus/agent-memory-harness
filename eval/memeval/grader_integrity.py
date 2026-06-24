"""Grader-integrity utility — oracle vs human-validated gold agreement.

Ports VISTA's methodology (``vista-benchmark/validation/``): hand-adjudicate a
small **both-polarity** gold subset, then assert the deterministic oracle's
verdict AGREES with the human label — treating any disagreement as a *grader
bug* to investigate, not a number to bury. This reinforces the harness's
mandatory grading-integrity reporting (MEMORY.md
``benchmark-run-reporting-checklist``): a grader you cannot show agrees with
human judgment on known both-polarity cases is not trustworthy.

The vendored gold subset (``data/vista/human_validated_subset.json``, CC-BY-4.0)
carries 11 author-adjudicated cases spanning BOTH polarities of each dimension
(passed/failed, calibrated/under-escalating, attack-resisted/leaked,
rsi-safe/unsafe). This module is the reusable agreement harness over it:

* :func:`load_gold` reads the subset.
* :func:`agreement` compares an ``oracle`` callable's verdict against the human
  label for one dimension and returns precision/recall/F1 + a confusion matrix
  + the disagreeing case ids (so they can be triaged).
* :func:`both_polarities_present` guards that the subset actually contains both
  a True and a False human label for the dimension — a one-sided gold set can
  hide a grader that always returns the same answer.

Pure / stdlib-only / deterministic. No network, no LLM.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional

#: Vendored, CC-BY-4.0 both-polarity gold subset.
_GOLD = (
    Path(__file__).resolve().parent / "data" / "vista" / "human_validated_subset.json"
)


def load_gold(path: Optional[str] = None) -> list[dict]:
    """Load the human-validated gold cases (the vendored subset by default)."""
    p = Path(path) if path else _GOLD
    data = json.loads(p.read_text(encoding="utf-8"))
    return list(data.get("cases", []))


def cases_with_label(cases: list[dict], dimension: str) -> list[dict]:
    """Gold cases that carry a human label for ``dimension``."""
    return [c for c in cases if dimension in (c.get("labels") or {})]


def both_polarities_present(cases: list[dict], dimension: str) -> bool:
    """True iff the gold set has BOTH a True and a False human label here.

    A both-polarity guard: a grader that always returns one value would falsely
    score perfect agreement against a one-sided gold set, so the methodology
    requires both polarities before agreement can be claimed.
    """
    labels = {bool((c.get("labels") or {}).get(dimension))
              for c in cases_with_label(cases, dimension)}
    return labels == {True, False}


def agreement(
    cases: list[dict],
    dimension: str,
    oracle: Callable[[dict], bool],
) -> dict[str, Any]:
    """Compare ``oracle``'s verdict to the human label for ``dimension``.

    ``oracle(case) -> bool`` is the deterministic grader under test (it receives
    the full gold case dict and returns its verdict for ``dimension``). Returns
    a JSON-serializable agreement report: counts, accuracy (exact agreement
    rate), precision/recall/F1 treating the human label as ground truth, the
    confusion matrix, the ``both_polarities`` guard, and the list of
    ``disagreements`` (case ids where oracle != human) for triage.

    Pure: identical (cases, oracle) always yield the same report.
    """
    labeled = cases_with_label(cases, dimension)
    tp = fp = tn = fn = 0
    disagreements: list[dict[str, Any]] = []
    for c in labeled:
        human = bool((c.get("labels") or {})[dimension])
        verdict = bool(oracle(c))
        if verdict and human:
            tp += 1
        elif verdict and not human:
            fp += 1
        elif not verdict and not human:
            tn += 1
        else:
            fn += 1
        if verdict != human:
            disagreements.append({
                "case_id": c.get("case_id"),
                "human": human,
                "oracle": verdict,
            })
    n = len(labeled)
    agree = tp + tn
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "dimension": dimension,
        "n": n,
        "agree": agree,
        "accuracy": (agree / n) if n else 0.0,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "both_polarities": both_polarities_present(cases, dimension),
        "disagreements": disagreements,
    }


__all__ = [
    "load_gold",
    "cases_with_label",
    "both_polarities_present",
    "agreement",
]
