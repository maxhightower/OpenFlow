"""Budget window endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from chloe.scheduler.engine import SchedulerEngine
from chloe.scheduler.estimator import CostEstimator
from chloe.scheduler.optimizer import BudgetOptimizer
from chloe.graph.engine import DAGEngine
from chloe.graph.models import ProjectDAG
from chloe.web.deps import get_store, get_config
from chloe.web.config import WebConfig
from chloe.web.store import WebStore

router = APIRouter(prefix="/api/budget", tags=["budget"])


def _get_scheduler(store: WebStore, config: WebConfig) -> SchedulerEngine:
    dag_engine = DAGEngine.from_project_dag(ProjectDAG(name="_budget"))
    estimator = CostEstimator(store)
    optimizer = BudgetOptimizer()
    return SchedulerEngine(
        dag_engine=dag_engine,
        store=store,
        estimator=estimator,
        optimizer=optimizer,
        token_budget_per_window=config.token_budget,
        model=config.model,
    )


@router.get("")
def get_budget_status(
    store: WebStore = Depends(get_store),
    config: WebConfig = Depends(get_config),
) -> dict:
    scheduler = _get_scheduler(store, config)
    return scheduler.window_summary()


@router.get("/history")
def get_budget_history(
    limit: int = 10,
    store: WebStore = Depends(get_store),
) -> list[dict]:
    rows = store._conn.execute(
        "SELECT * FROM budget_windows ORDER BY started_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "window_id": r["window_id"],
            "started_at": r["started_at"],
            "token_budget": r["token_budget"],
            "tokens_used": r["tokens_used"],
            "total_cost": r["total_cost"],
        }
        for r in rows
    ]


@router.get("/recommend-model")
def recommend_model(
    expected_tokens: int = 20_000,
    usd_budget_remaining: float | None = None,
    store: WebStore = Depends(get_store),
    config: WebConfig = Depends(get_config),
) -> dict:
    """
    Suggest the best Claude model for the current 5-hour window.

    The selector compares the fraction of tokens remaining against the
    fraction of time remaining and picks Haiku/Sonnet/Opus to maximise
    budget utilisation. More expensive models are favoured when there is
    little time left and unused tokens would otherwise evaporate.
    """
    scheduler = _get_scheduler(store, config)
    recommendation = scheduler.recommend_model(
        expected_tokens_per_task=expected_tokens,
        usd_budget_remaining=usd_budget_remaining,
    )
    return recommendation.to_dict()
