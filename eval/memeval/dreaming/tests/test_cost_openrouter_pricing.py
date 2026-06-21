"""Cost-tracking integration tests — halliday Finding #6.

Verify ``memeval.cost.PRICING`` has entries for the OpenRouter models
ADR-dreaming-004 commits to. Without these entries, OpenRouter spend
silently reports as $0 (the `_FALLBACK_PRICE` is `0.0/0.0`), defeating
ADR-dreaming-004's "wires into cost.py" policy.
"""

from __future__ import annotations

import pytest

from memeval.cost import PRICING, cost_of, price_for


@pytest.mark.parametrize(
    "model,expected_in,expected_out",
    [
        # v1 default per ADR-dreaming-004.
        ("inclusionai/ling-2.6-flash", 0.01, 0.03),
        # Trade-up targets per ADR-dreaming-004.
        ("deepseek/deepseek-v4-flash", 0.09, 0.18),
        ("xiaomi/mimo-v2.5", 0.14, 0.28),
        ("deepseek/deepseek-v4-pro", 0.435, 0.87),
    ],
)
def test_openrouter_pricing_entries_match_adr_004(
    model: str, expected_in: float, expected_out: float
) -> None:
    assert model in PRICING, f"missing PRICING entry for {model}"
    assert PRICING[model] == {"in": expected_in, "out": expected_out}


def test_default_daydream_model_has_pricing_entry() -> None:
    """The default DREAM_MODEL must be priced or cost tracking lies."""
    from memeval.dreaming.llm import DEFAULT_MODEL

    prices = price_for(DEFAULT_MODEL)
    assert prices != {"in": 0.0, "out": 0.0}, (
        f"DEFAULT_MODEL={DEFAULT_MODEL!r} falls back to $0 pricing — "
        f"add a PRICING entry per ADR-dreaming-004 / halliday Finding #6"
    )


def test_cost_of_default_model_is_nonzero_for_nonzero_tokens() -> None:
    from memeval.dreaming.llm import DEFAULT_MODEL

    cost = cost_of(DEFAULT_MODEL, tokens_in=1_000_000, tokens_out=1_000_000)
    assert cost > 0, "default Daydream model must produce nonzero cost"
