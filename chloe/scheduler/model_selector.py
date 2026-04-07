"""Pick the best Claude model for the remaining time and budget in a 5h window.

The 5-hour subscription window has two finite resources: time and tokens.
A naive scheduler picks one model up front and burns through it. That leaves
budget on the table when time runs out, or runs out of tokens early when the
chosen model is too expensive.

This module computes "headroom" — the difference between the fraction of
tokens remaining and the fraction of time remaining — and chooses a model
that maximises utilisation:

    headroom > 0  : tokens are abundant relative to time → upgrade (Opus)
    headroom ≈ 0  : balanced → keep the default (Sonnet)
    headroom < 0  : tokens are scarce relative to time → downgrade (Haiku)

When time is nearly gone, the unused token budget will evaporate with the
window reset, so the selector aggressively upgrades. When tokens are nearly
gone, it pins to Haiku to stretch what is left.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from chloe.observer.parser import COST_PER_INPUT_TOKEN, COST_PER_OUTPUT_TOKEN
from chloe.scheduler.models import BudgetWindow, WINDOW_DURATION_SECONDS


# Cheapest -> most capable. Index in this list also implies tier.
MODEL_TIERS: list[str] = [
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
]

HAIKU = MODEL_TIERS[0]
SONNET = MODEL_TIERS[1]
OPUS = MODEL_TIERS[2]

# Below this many tokens remaining, always pin to Haiku to stretch.
HARD_TOKEN_FLOOR = 5_000

# Below this many seconds remaining in the window, splurge on the most
# capable affordable model — leftover budget evaporates with the reset.
SPLURGE_TIME_THRESHOLD_SECONDS = 15 * 60

# Headroom thresholds for tier selection.
UPGRADE_HEADROOM = 0.20      # >= this much surplus → Opus
BALANCED_HEADROOM = -0.10    # >= this → Sonnet, otherwise → Haiku

# Default split between input and output tokens when estimating per-task cost.
# Most coding/agent workloads are input-heavy.
DEFAULT_OUTPUT_FRACTION = 0.30


@dataclass
class ModelRecommendation:
    """Result of a model selection decision."""

    model: str
    reason: str
    headroom: float
    time_fraction_remaining: float
    token_fraction_remaining: float
    estimated_cost_usd: float | None
    fits_in_usd_budget: bool
    candidates: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "reason": self.reason,
            "headroom": round(self.headroom, 4),
            "time_fraction_remaining": round(self.time_fraction_remaining, 4),
            "token_fraction_remaining": round(self.token_fraction_remaining, 4),
            "estimated_cost_usd": self.estimated_cost_usd,
            "fits_in_usd_budget": self.fits_in_usd_budget,
            "candidates": self.candidates,
        }


def estimate_task_cost_usd(
    model: str,
    total_tokens: int,
    output_fraction: float = DEFAULT_OUTPUT_FRACTION,
) -> float:
    """Approximate the USD cost of running `total_tokens` against `model`."""
    if total_tokens <= 0:
        return 0.0
    out_tok = int(total_tokens * output_fraction)
    in_tok = total_tokens - out_tok
    in_rate = COST_PER_INPUT_TOKEN.get(model, 0.0)
    out_rate = COST_PER_OUTPUT_TOKEN.get(model, 0.0)
    return round(in_tok * in_rate + out_tok * out_rate, 6)


class ModelSelector:
    """Selects the best Claude model based on remaining time and budget.

    Designed to be cheap to call repeatedly — every decision is a pure
    function of the BudgetWindow and a few optional inputs, no I/O.
    """

    def __init__(
        self,
        available_models: list[str] | None = None,
        token_floor: int = HARD_TOKEN_FLOOR,
        splurge_threshold_seconds: int = SPLURGE_TIME_THRESHOLD_SECONDS,
        upgrade_headroom: float = UPGRADE_HEADROOM,
        balanced_headroom: float = BALANCED_HEADROOM,
    ) -> None:
        self.available_models = list(available_models) if available_models else list(MODEL_TIERS)
        self.token_floor = token_floor
        self.splurge_threshold_seconds = splurge_threshold_seconds
        self.upgrade_headroom = upgrade_headroom
        self.balanced_headroom = balanced_headroom

    # ------------------------------------------------------------------ public

    def select(
        self,
        window: BudgetWindow,
        *,
        expected_tokens_per_task: int = 20_000,
        usd_budget_remaining: float | None = None,
    ) -> ModelRecommendation:
        """Return a model recommendation for the current window state."""
        seconds_remaining = window.remaining_seconds
        token_budget = max(window.token_budget, 1)
        tokens_remaining = window.tokens_remaining

        time_frac = seconds_remaining / WINDOW_DURATION_SECONDS
        token_frac = tokens_remaining / token_budget
        headroom = token_frac - time_frac

        candidates = self._build_candidates(
            expected_tokens_per_task, usd_budget_remaining
        )

        # 1. Hard token floor — preserve what little is left.
        if tokens_remaining <= self.token_floor:
            chosen = self._cheapest_available()
            reason = (
                f"Token budget nearly exhausted ({tokens_remaining:,} <= "
                f"{self.token_floor:,}); pinning to cheapest model."
            )
            return self._make_recommendation(
                chosen, reason, headroom, time_frac, token_frac,
                candidates, usd_budget_remaining,
            )

        # 2. Time nearly gone with tokens still on the table — splurge.
        if (
            seconds_remaining <= self.splurge_threshold_seconds
            and tokens_remaining > self.token_floor * 4
        ):
            chosen = self._most_capable_affordable(
                candidates, usd_budget_remaining
            )
            mins = seconds_remaining / 60
            reason = (
                f"Only {mins:.1f} min left in window with {tokens_remaining:,} "
                f"tokens unused; upgrading to {chosen} so unused budget is "
                "not lost when the window resets."
            )
            return self._make_recommendation(
                chosen, reason, headroom, time_frac, token_frac,
                candidates, usd_budget_remaining,
            )

        # 3. Headroom-based tier selection.
        if headroom >= self.upgrade_headroom:
            preferred = OPUS
            why = "large surplus token budget vs time remaining"
        elif headroom >= self.balanced_headroom:
            preferred = SONNET
            why = "tokens and time roughly balanced"
        else:
            preferred = HAIKU
            why = "token usage outpacing elapsed time; conserving budget"

        chosen = self._closest_available(preferred)

        # 4. USD budget constraint — downgrade until the per-task cost fits.
        chosen = self._downgrade_to_fit(chosen, candidates, usd_budget_remaining)

        reason = (
            f"headroom={headroom:+.2f} "
            f"(tokens_remaining={token_frac * 100:.0f}%, "
            f"time_remaining={time_frac * 100:.0f}%) → {chosen}: {why}"
        )
        return self._make_recommendation(
            chosen, reason, headroom, time_frac, token_frac,
            candidates, usd_budget_remaining,
        )

    # ----------------------------------------------------------------- helpers

    def _build_candidates(
        self,
        expected_tokens: int,
        usd_budget_remaining: float | None,
    ) -> dict[str, dict]:
        per_task = expected_tokens if expected_tokens > 0 else 20_000
        candidates: dict[str, dict] = {}
        for m in self.available_models:
            cost = estimate_task_cost_usd(m, per_task)
            candidates[m] = {
                "tier_index": MODEL_TIERS.index(m) if m in MODEL_TIERS else -1,
                "estimated_cost_usd_per_task": cost,
                "fits_in_usd_budget": (
                    usd_budget_remaining is None or cost <= usd_budget_remaining
                ),
            }
        return candidates

    def _cheapest_available(self) -> str:
        for m in MODEL_TIERS:
            if m in self.available_models:
                return m
        return self.available_models[0]

    def _most_capable_affordable(
        self,
        candidates: dict[str, dict],
        usd_budget_remaining: float | None,
    ) -> str:
        for m in reversed(MODEL_TIERS):
            if m in self.available_models and (
                usd_budget_remaining is None
                or candidates[m]["fits_in_usd_budget"]
            ):
                return m
        return self._cheapest_available()

    def _closest_available(self, preferred: str) -> str:
        if preferred in self.available_models:
            return preferred
        # Walk down to the next-cheapest available tier.
        if preferred not in MODEL_TIERS:
            return self._cheapest_available()
        idx = MODEL_TIERS.index(preferred)
        for i in range(idx, -1, -1):
            if MODEL_TIERS[i] in self.available_models:
                return MODEL_TIERS[i]
        # Otherwise walk up.
        for i in range(idx + 1, len(MODEL_TIERS)):
            if MODEL_TIERS[i] in self.available_models:
                return MODEL_TIERS[i]
        return self._cheapest_available()

    def _downgrade_to_fit(
        self,
        chosen: str,
        candidates: dict[str, dict],
        usd_budget_remaining: float | None,
    ) -> str:
        if usd_budget_remaining is None:
            return chosen
        if chosen not in candidates:
            return chosen
        if candidates[chosen]["fits_in_usd_budget"]:
            return chosen
        idx = MODEL_TIERS.index(chosen) if chosen in MODEL_TIERS else -1
        for i in range(idx - 1, -1, -1):
            m = MODEL_TIERS[i]
            if m in self.available_models and candidates[m]["fits_in_usd_budget"]:
                return m
        # Even the cheapest model exceeds the USD budget — return it anyway
        # so callers can surface the infeasibility via fits_in_usd_budget.
        return self._cheapest_available()

    def _make_recommendation(
        self,
        chosen: str,
        reason: str,
        headroom: float,
        time_frac: float,
        token_frac: float,
        candidates: dict[str, dict],
        usd_budget_remaining: float | None,
    ) -> ModelRecommendation:
        info = candidates.get(chosen, {})
        cost = info.get("estimated_cost_usd_per_task")
        fits = info.get("fits_in_usd_budget", True)
        return ModelRecommendation(
            model=chosen,
            reason=reason,
            headroom=headroom,
            time_fraction_remaining=time_frac,
            token_fraction_remaining=token_frac,
            estimated_cost_usd=cost,
            fits_in_usd_budget=fits,
            candidates=candidates,
        )
