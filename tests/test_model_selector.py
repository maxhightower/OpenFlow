"""Tests for ModelSelector — time/budget-aware Claude model selection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from chloe.scheduler.model_selector import (
    HAIKU,
    HARD_TOKEN_FLOOR,
    OPUS,
    SONNET,
    ModelSelector,
    estimate_task_cost_usd,
)
from chloe.scheduler.models import WINDOW_DURATION_SECONDS, BudgetWindow


def _window(
    *,
    elapsed_seconds: float = 0.0,
    token_budget: int = 500_000,
    tokens_used: int = 0,
) -> BudgetWindow:
    """Build a BudgetWindow whose started_at is `elapsed_seconds` ago."""
    started = datetime.now(timezone.utc) - timedelta(seconds=elapsed_seconds)
    return BudgetWindow(
        window_id="w1",
        started_at=started,
        token_budget=token_budget,
        total_tokens_used=tokens_used,
    )


# ---------------------------------------------------------------------------
# estimate_task_cost_usd
# ---------------------------------------------------------------------------


class TestEstimateTaskCost:
    def test_zero_tokens_is_zero(self):
        assert estimate_task_cost_usd(SONNET, 0) == 0.0

    def test_opus_more_expensive_than_sonnet(self):
        assert estimate_task_cost_usd(OPUS, 10_000) > estimate_task_cost_usd(SONNET, 10_000)

    def test_sonnet_more_expensive_than_haiku(self):
        assert estimate_task_cost_usd(SONNET, 10_000) > estimate_task_cost_usd(HAIKU, 10_000)

    def test_unknown_model_returns_zero(self):
        assert estimate_task_cost_usd("not-a-model", 10_000) == 0.0


# ---------------------------------------------------------------------------
# Headroom-based selection
# ---------------------------------------------------------------------------


class TestHeadroomSelection:
    def test_fresh_window_balanced_picks_sonnet(self):
        # Brand new window: time_frac=1.0, token_frac=1.0 → headroom=0 → Sonnet.
        window = _window(elapsed_seconds=0, token_budget=500_000, tokens_used=0)
        rec = ModelSelector().select(window)
        assert rec.model == SONNET
        assert rec.headroom == pytest.approx(0.0, abs=0.001)

    def test_token_surplus_upgrades_to_opus(self):
        # 4 hours elapsed (20% time left) but only 10% tokens used (90% left).
        # headroom = 0.90 - 0.20 = 0.70 → Opus.
        window = _window(
            elapsed_seconds=4 * 3600,
            token_budget=500_000,
            tokens_used=50_000,
        )
        rec = ModelSelector().select(window)
        assert rec.model == OPUS
        assert rec.headroom > 0.5

    def test_token_deficit_downgrades_to_haiku(self):
        # 1 hour elapsed (80% time left) but 95% tokens used (5% left).
        # headroom = 0.05 - 0.80 = -0.75 → Haiku.
        window = _window(
            elapsed_seconds=1 * 3600,
            token_budget=500_000,
            tokens_used=475_000,
        )
        rec = ModelSelector().select(window)
        assert rec.model == HAIKU
        assert rec.headroom < -0.5

    def test_balanced_burn_picks_sonnet(self):
        # 50% elapsed, 50% used → headroom 0 → Sonnet.
        window = _window(
            elapsed_seconds=2.5 * 3600,
            token_budget=500_000,
            tokens_used=250_000,
        )
        rec = ModelSelector().select(window)
        assert rec.model == SONNET


# ---------------------------------------------------------------------------
# Hard floors and splurge logic
# ---------------------------------------------------------------------------


class TestHardFloors:
    def test_token_floor_pins_to_haiku(self):
        # Even with lots of time, near-empty token budget pins to Haiku.
        window = _window(
            elapsed_seconds=0,
            token_budget=500_000,
            tokens_used=499_000,  # only 1k tokens left, well below 5k floor
        )
        rec = ModelSelector().select(window)
        assert rec.model == HAIKU
        assert "exhausted" in rec.reason.lower() or "nearly" in rec.reason.lower()

    def test_token_floor_overrides_short_time(self):
        # Time almost gone too — token floor still wins.
        window = _window(
            elapsed_seconds=WINDOW_DURATION_SECONDS - 5 * 60,
            token_budget=500_000,
            tokens_used=499_000,
        )
        rec = ModelSelector().select(window)
        assert rec.model == HAIKU

    def test_splurge_when_time_almost_gone(self):
        # 10 minutes left, half the tokens still unused → upgrade to Opus.
        window = _window(
            elapsed_seconds=WINDOW_DURATION_SECONDS - 10 * 60,
            token_budget=500_000,
            tokens_used=250_000,
        )
        rec = ModelSelector().select(window)
        assert rec.model == OPUS
        assert "min left" in rec.reason

    def test_splurge_does_not_trigger_if_tokens_low(self):
        # 10 minutes left but only 10k tokens (just above floor) → no splurge,
        # so we should not jump to Opus.
        window = _window(
            elapsed_seconds=WINDOW_DURATION_SECONDS - 10 * 60,
            token_budget=500_000,
            tokens_used=490_000,
        )
        rec = ModelSelector().select(window)
        assert rec.model != OPUS


# ---------------------------------------------------------------------------
# USD budget constraint
# ---------------------------------------------------------------------------


class TestUsdBudgetConstraint:
    def test_tight_usd_budget_downgrades_from_opus(self):
        # Headroom would normally pick Opus, but USD budget is too small.
        window = _window(
            elapsed_seconds=4 * 3600,
            token_budget=500_000,
            tokens_used=50_000,
        )
        # 50k tokens against Opus would cost ~$2 — pass a tighter budget.
        rec = ModelSelector().select(
            window,
            expected_tokens_per_task=50_000,
            usd_budget_remaining=0.20,  # too tight for Opus
        )
        assert rec.model in (SONNET, HAIKU)
        assert rec.estimated_cost_usd <= 0.20 or rec.model == HAIKU

    def test_unlimited_usd_budget_does_not_constrain(self):
        window = _window(
            elapsed_seconds=4 * 3600,
            token_budget=500_000,
            tokens_used=50_000,
        )
        rec = ModelSelector().select(window, usd_budget_remaining=None)
        assert rec.model == OPUS

    def test_candidates_report_per_model_cost(self):
        window = _window(elapsed_seconds=0, token_budget=500_000, tokens_used=0)
        rec = ModelSelector().select(window, expected_tokens_per_task=10_000)
        assert HAIKU in rec.candidates
        assert SONNET in rec.candidates
        assert OPUS in rec.candidates
        # Costs should be ordered: Haiku < Sonnet < Opus
        haiku_cost = rec.candidates[HAIKU]["estimated_cost_usd_per_task"]
        sonnet_cost = rec.candidates[SONNET]["estimated_cost_usd_per_task"]
        opus_cost = rec.candidates[OPUS]["estimated_cost_usd_per_task"]
        assert haiku_cost < sonnet_cost < opus_cost


# ---------------------------------------------------------------------------
# Available models filtering
# ---------------------------------------------------------------------------


class TestAvailableModels:
    def test_restricted_to_subset(self):
        # If Opus isn't available, surplus headroom falls back to Sonnet.
        selector = ModelSelector(available_models=[HAIKU, SONNET])
        window = _window(
            elapsed_seconds=4 * 3600,
            token_budget=500_000,
            tokens_used=50_000,
        )
        rec = selector.select(window)
        assert rec.model == SONNET

    def test_only_haiku_available(self):
        selector = ModelSelector(available_models=[HAIKU])
        window = _window(
            elapsed_seconds=4 * 3600,
            token_budget=500_000,
            tokens_used=50_000,
        )
        rec = selector.select(window)
        assert rec.model == HAIKU


# ---------------------------------------------------------------------------
# Recommendation serialisation
# ---------------------------------------------------------------------------


class TestRecommendationSerialization:
    def test_to_dict_round_trip(self):
        window = _window(
            elapsed_seconds=2 * 3600,
            token_budget=500_000,
            tokens_used=200_000,
        )
        rec = ModelSelector().select(window)
        d = rec.to_dict()
        assert d["model"] == rec.model
        assert d["headroom"] == round(rec.headroom, 4)
        assert "candidates" in d
        assert "reason" in d
        assert "time_fraction_remaining" in d
        assert "token_fraction_remaining" in d


# ---------------------------------------------------------------------------
# Integration with SchedulerEngine
# ---------------------------------------------------------------------------


class TestSchedulerEngineIntegration:
    def test_recommend_model_method(self, tmp_path):
        from chloe.graph.engine import DAGEngine
        from chloe.graph.models import ProjectDAG
        from chloe.scheduler.engine import SchedulerEngine

        dag_engine = DAGEngine.from_project_dag(ProjectDAG(name="t"))
        engine = SchedulerEngine.from_dag_engine(
            dag_engine,
            db_path=tmp_path / "scheduler.db",
            token_budget=500_000,
        )
        rec = engine.recommend_model(expected_tokens_per_task=10_000)
        # Fresh window: balanced → Sonnet
        assert rec.model == SONNET

    def test_auto_select_changes_engine_model(self, tmp_path):
        from chloe.graph.engine import DAGEngine
        from chloe.graph.models import ProjectDAG, Task, TaskType
        from chloe.scheduler.engine import SchedulerEngine

        dag = ProjectDAG(
            name="t",
            tasks=[
                Task(
                    id="t1",
                    name="T1",
                    task_type=TaskType.FEATURE,
                    estimated_tokens=10_000,
                )
            ],
        )
        dag_engine = DAGEngine.from_project_dag(dag)
        engine = SchedulerEngine.from_dag_engine(
            dag_engine,
            db_path=tmp_path / "scheduler.db",
            token_budget=500_000,
            model=HAIKU,
            auto_select_model=True,
        )
        # Force the window to look "almost over" so Opus splurge triggers.
        # save_window only updates tokens/cost on conflict, so write directly.
        window = engine.current_window()
        forced_start = (
            datetime.now(timezone.utc)
            - timedelta(seconds=WINDOW_DURATION_SECONDS - 5 * 60)
        )
        engine.store._conn.execute(
            "UPDATE budget_windows SET started_at = ? WHERE window_id = ?",
            (forced_start.isoformat(), window.window_id),
        )
        engine.store._conn.commit()

        engine.build_schedule()
        assert engine.last_recommendation is not None
        assert engine.model == engine.last_recommendation.model
        # 5 min left + plenty of tokens → splurge to Opus
        assert engine.model == OPUS
