"""Budget window endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from claude_flow.scheduler.engine import SchedulerEngine
from claude_flow.scheduler.estimator import CostEstimator
from claude_flow.scheduler.optimizer import BudgetOptimizer
from claude_flow.graph.engine import DAGEngine
from claude_flow.graph.models import ProjectDAG
from claude_flow.web.deps import get_store, get_config
from claude_flow.web.config import WebConfig
from claude_flow.web.store import WebStore

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
