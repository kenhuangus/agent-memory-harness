"""Cost tracking + budget enforcement for the memory-harness evaluation.

Stdlib-only (``json``, ``pathlib``, ``typing``). No heavy deps, no network --
this module gates real (paid) runs so it must import cleanly on Python 3.11+
with nothing installed.

Unit convention (load-bearing, see CONTRACT.md invariant #2)
------------------------------------------------------------
**All prices are USD per MILLION tokens.** ``PRICING[model]["in"]`` /
``["out"]``, :class:`~memeval.schema.ModelConfig.price_in` / ``price_out`` and
:class:`~memeval.protocols.ModelAdapter.price_in` / ``price_out`` all use this
one unit. The cost of a call is therefore::

    cost_usd = tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out

What lives here
---------------
PRICING         per-model $/Mtok table (live Anthropic prices, see prd.md §7).
DEFAULT_BUDGET_USD  default $10 hard cap when no budget is supplied (PRD §7.3).
price_for       safe lookup with a sensible fallback.
cost_of         price one (model, tokens_in, tokens_out) call in USD.
BudgetExceeded  raised by CostTracker.add when a budget is overrun.
CostTracker     running spend/token accounting with optional hard budgets.
load_key_config reads config/keys.example.json (captain -> key env + budget).
cheapest_first  orders ModelConfigs Haiku+mem -> Haiku -> Sonnet -> Opus.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .schema import ModelConfig

# --------------------------------------------------------------------------- #
# Default budget
# --------------------------------------------------------------------------- #
#: Default hard USD cap for a run when the caller (CLI / Action / driver) does
#: not specify one. $10 matches the PRD's per-run budget (PRD §7.3): it bounds an
#: accidental runaway run while still covering the default smoke/floor sweeps. A
#: larger sweep (e.g. the expensive code benches at their full group-aware floors)
#: should pass an explicit ``--budget-usd`` rather than relying on the default.
#: A value <= 0 is treated by the CLIs as "no cap" (pure accounting).
DEFAULT_BUDGET_USD: float = 10.0

# --------------------------------------------------------------------------- #
# Pricing table
# --------------------------------------------------------------------------- #
# LIVE PRICES -- USD per MILLION tokens. Confirmed against the Anthropic price
# list (2026-06; see prd.md section 7). Keys are model ids; values are
# {"in": $/Mtok input, "out": $/Mtok output}. Re-confirm against the console
# price sheet before a large paid run -- list prices do change.
PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {"in": 1.00, "out": 5.00},    # Haiku 4.5
    "claude-sonnet-4-6": {"in": 3.00, "out": 15.00},  # Sonnet 4.6
    "claude-opus-4-8": {"in": 5.00, "out": 25.00},    # Opus 4.8
    "echo": {"in": 0.0, "out": 0.0},                  # offline adapter: free
    # OpenRouter models -- subconscious-side (Daydream + Dreaming) per
    # ADR-dreaming-004. Closes halliday Finding #6: without these entries,
    # Daydream spend silently reports as $0 and the PRD <~10% memory-token
    # overhead is uncheckable. Re-verify pricing via OpenRouter /api/v1/models
    # before a large paid run -- OpenRouter rotates pricing periodically.
    "inclusionai/ling-2.6-flash": {"in": 0.01, "out": 0.03},   # v1 default
    "deepseek/deepseek-v4-flash": {"in": 0.09, "out": 0.18},   # trade-up target
    "xiaomi/mimo-v2.5": {"in": 0.14, "out": 0.28},
    "deepseek/deepseek-v4-pro": {"in": 0.435, "out": 0.87},
}

#: Fallback price used when a model id is unknown to :data:`PRICING`. Zero so an
#: unpriced/offline model never silently inflates spend; real models should
#: always be present in the table above.
_FALLBACK_PRICE: dict[str, float] = {"in": 0.0, "out": 0.0}

#: Cheapest-first tier ranking (index = priority; lower = cheaper = tried first).
_TIER_RANK: dict[str, int] = {"haiku": 0, "sonnet": 1, "opus": 2}


# --------------------------------------------------------------------------- #
# Pricing helpers
# --------------------------------------------------------------------------- #
def price_for(model: str) -> dict[str, float]:
    """Return the ``{"in", "out"}`` $/Mtok prices for ``model``.

    Falls back to a zero-price entry for unknown ids so callers never KeyError;
    real models should be registered in :data:`PRICING`. Substring matching is
    applied so e.g. ``"claude-haiku-4-5-20991231"`` resolves to the base
    ``"claude-haiku-4-5"`` entry.
    """
    if model in PRICING:
        return dict(PRICING[model])
    # Tolerate dated/suffixed ids (e.g. "claude-opus-4-8-20991231").
    for known, price in PRICING.items():
        if known != "echo" and known in model:
            return dict(price)
    return dict(_FALLBACK_PRICE)


def cost_of(model: str, tokens_in: int, tokens_out: int) -> float:
    """Cost in USD of one call: ``tin/1e6*price_in + tout/1e6*price_out``.

    Prices are $/Mtok (see module docstring). Negative token counts are clamped
    to zero so a malformed step cannot produce a negative (budget-extending)
    cost.
    """
    price = price_for(model)
    tin = max(0, tokens_in)
    tout = max(0, tokens_out)
    return tin / 1_000_000 * price["in"] + tout / 1_000_000 * price["out"]


# --------------------------------------------------------------------------- #
# Budget enforcement
# --------------------------------------------------------------------------- #
class BudgetExceeded(Exception):
    """Raised by :meth:`CostTracker.add` when a run would overrun its budget.

    Carries the numbers needed to record a partial :class:`RunResult`:
    ``spent_usd`` (running spend INCLUDING the offending call), ``budget_usd``
    (the dollar cap, or ``-1`` when only a token cap tripped) and ``tokens``
    (running total tokens including the offending call).
    """

    def __init__(
        self,
        message: str,
        *,
        spent_usd: float,
        budget_usd: float,
        tokens: int = 0,
    ) -> None:
        super().__init__(message)
        self.spent_usd = spent_usd
        self.budget_usd = budget_usd
        self.tokens = tokens


class CostTracker:
    """Running spend + token accounting with optional hard budgets.

    Pass ``budget_usd`` and/or ``budget_tokens`` to cap a run; pass neither for
    pure accounting (never raises). :meth:`add` returns the new running
    ``spent_usd`` and raises :class:`BudgetExceeded` *after* recording the call
    if either cap is exceeded -- so ``spent_usd`` / ``total_tokens_*`` always
    reflect the call that tripped the limit (the harness reads them to emit a
    ``partial`` RunResult).

    Determinism: no wall-clock, no global state -- spend is a pure function of
    the ``add`` calls made. A custom ``pricing`` table may be injected (tests).
    """

    def __init__(
        self,
        budget_usd: Optional[float] = None,
        budget_tokens: Optional[int] = None,
        pricing: Optional[dict] = None,
    ) -> None:
        self.budget_usd = budget_usd
        self.budget_tokens = budget_tokens
        self.pricing = pricing  # None -> use module-level PRICING via cost_of
        self.spent_usd: float = 0.0
        self.total_tokens_in: int = 0
        self.total_tokens_out: int = 0
        self.n_calls: int = 0

    # -- introspection ----------------------------------------------------- #
    @property
    def spent_tokens(self) -> int:
        """Total tokens billed so far (input + output)."""
        return self.total_tokens_in + self.total_tokens_out

    def _cost_of(self, model: str, tokens_in: int, tokens_out: int) -> float:
        """Price one call, honoring an injected ``pricing`` override if set."""
        if self.pricing is None:
            return cost_of(model, tokens_in, tokens_out)
        price = self.pricing.get(model, _FALLBACK_PRICE)
        tin = max(0, tokens_in)
        tout = max(0, tokens_out)
        return tin / 1_000_000 * price.get("in", 0.0) + tout / 1_000_000 * price.get("out", 0.0)

    # -- recording --------------------------------------------------------- #
    def add(self, model: str, tokens_in: int, tokens_out: int) -> float:
        """Record one model call; return the new running ``spent_usd``.

        Adds the call's cost and tokens to the running totals, then raises
        :class:`BudgetExceeded` (totals already updated) if ``spent_usd`` or
        ``spent_tokens`` now exceeds its cap. Token counts are clamped to >= 0.
        """
        tin = max(0, tokens_in)
        tout = max(0, tokens_out)
        call_cost = self._cost_of(model, tin, tout)

        self.spent_usd += call_cost
        self.total_tokens_in += tin
        self.total_tokens_out += tout
        self.n_calls += 1

        if self.budget_usd is not None and self.spent_usd > self.budget_usd:
            raise BudgetExceeded(
                f"USD budget exceeded: spent ${self.spent_usd:.6f} > "
                f"budget ${self.budget_usd:.6f} after call to {model!r}",
                spent_usd=self.spent_usd,
                budget_usd=self.budget_usd,
                tokens=self.spent_tokens,
            )
        if self.budget_tokens is not None and self.spent_tokens > self.budget_tokens:
            raise BudgetExceeded(
                f"Token budget exceeded: {self.spent_tokens} > "
                f"{self.budget_tokens} after call to {model!r}",
                spent_usd=self.spent_usd,
                budget_usd=self.budget_usd if self.budget_usd is not None else -1.0,
                tokens=self.spent_tokens,
            )
        return self.spent_usd

    # -- planning / guards ------------------------------------------------- #
    def remaining_usd(self) -> Optional[float]:
        """Dollars left before the USD cap (``None`` when no USD budget set)."""
        if self.budget_usd is None:
            return None
        return self.budget_usd - self.spent_usd

    def remaining_tokens(self) -> Optional[int]:
        """Tokens left before the token cap (``None`` when no token budget)."""
        if self.budget_tokens is None:
            return None
        return self.budget_tokens - self.spent_tokens

    def would_exceed(self, model: str, tokens_in: int, tokens_out: int) -> bool:
        """True if recording this call WOULD trip a budget -- without recording.

        Lets the harness skip/queue a call instead of catching the exception
        after the fact. Checks both the USD and token caps.
        """
        prospective_cost = self.spent_usd + self._cost_of(model, tokens_in, tokens_out)
        prospective_tokens = self.spent_tokens + max(0, tokens_in) + max(0, tokens_out)
        if self.budget_usd is not None and prospective_cost > self.budget_usd:
            return True
        if self.budget_tokens is not None and prospective_tokens > self.budget_tokens:
            return True
        return False

    def snapshot(self) -> dict[str, Any]:
        """JSON-serializable view of current spend, tokens, and budgets."""
        return {
            "spent_usd": self.spent_usd,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "spent_tokens": self.spent_tokens,
            "n_calls": self.n_calls,
            "budget_usd": self.budget_usd,
            "budget_tokens": self.budget_tokens,
            "remaining_usd": self.remaining_usd(),
            "remaining_tokens": self.remaining_tokens(),
        }


# --------------------------------------------------------------------------- #
# Per-key / per-captain config (sharded eval)
# --------------------------------------------------------------------------- #
def load_key_config(path: str | Path) -> dict[str, dict[str, Any]]:
    """Read ``config/keys.example.json`` -> ``{benchmark: {...}}``.

    Maps each captain/benchmark to its run config -- at minimum
    ``api_key_env`` (the env var holding that captain's Anthropic key) and
    ``budget_usd``; ``budget_tokens`` and ``captain`` are optional. Stdlib
    ``json`` only -- no schema validation beyond "top level is an object of
    objects" so the file stays easy to hand-edit.

    Captains (baked into keys.example.json):
      SWE-Bench-CL=Keith, LongMemEval=Ken, SWE-ContextBench=Brent,
      MemoryAgentBench=Scott B.

    Keys whose name starts with ``_`` are treated as comments and skipped
    (lets the example file carry inline documentation as a ``"_comment"`` field
    while staying valid JSON).
    """
    p = Path(path)
    with p.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"key config must be a JSON object, got {type(data).__name__}: {p}")
    out: dict[str, dict[str, Any]] = {}
    for key, value in data.items():
        if str(key).startswith("_"):
            continue  # comment field (e.g. "_comment")
        if not isinstance(value, dict):
            raise ValueError(f"key config entry {key!r} must be an object, got {type(value).__name__}")
        out[str(key)] = dict(value)
    return out


# --------------------------------------------------------------------------- #
# Cheapest-first ordering
# --------------------------------------------------------------------------- #
def cheapest_first(configs: list[ModelConfig]) -> list[ModelConfig]:
    """Order configs cheapest-capable-first: Haiku+mem -> Haiku -> Sonnet -> Opus.

    The harness tries the cheapest configuration that might already hit the
    accuracy target before paying for a bigger model. Ordering is **tier-primary,
    memory-secondary** -- the contract's stated order "Haiku+mem -> Haiku ->
    Sonnet -> Opus": climb tiers from cheapest, and within a tier evaluate the
    memory-ON variant before the no-memory baseline (the harness's whole bet is
    that cheap+memory wins). This keeps :func:`memeval.harness.cheapest_first`
    (the canonical, package-root export) and this helper in lock-step; a
    memory-primary order would (wrongly) try Sonnet+mem / Opus+mem before the
    cheaper Haiku no-memory baseline. Ordering key, ascending:

      1. tier rank haiku < sonnet < opus (from ``tier``; unknown/blank tiers
         sort last, then alphabetically by tier name);
      2. memory-ON before memory-OFF *within* a tier;
      3. blended price (price_in + price_out) ascending as a tie-break;
      4. name, for full determinism.

    Stable and non-mutating: returns a new list, leaves the input untouched.
    """
    def sort_key(cfg: ModelConfig) -> tuple[int, str, int, float, str]:
        tier = (cfg.tier or "").strip().lower()
        tier_rank = _TIER_RANK.get(tier, len(_TIER_RANK))
        return (
            tier_rank,                      # 1: haiku < sonnet < opus
            tier,                           # 1b: stable order for unknown tiers
            0 if cfg.memory else 1,         # 2: memory-on first within a tier
            cfg.price_in + cfg.price_out,   # 3: cheaper blended price first
            cfg.name,                       # 4: deterministic final tie-break
        )

    return sorted(configs, key=sort_key)


__all__ = [
    "PRICING",
    "DEFAULT_BUDGET_USD",
    "price_for",
    "cost_of",
    "BudgetExceeded",
    "CostTracker",
    "load_key_config",
    "cheapest_first",
]
