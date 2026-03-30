"""Analytics endpoints wrapping UsageStore queries."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from claude_flow.observer.store import UsageStore
from claude_flow.web.deps import get_usage_store

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/usage/hourly")
def usage_by_hour(store: UsageStore = Depends(get_usage_store)) -> list[dict]:
    return store.usage_by_hour()


@router.get("/usage/daily")
def usage_by_date(
    limit: int = 60,
    store: UsageStore = Depends(get_usage_store),
) -> list[dict]:
    return store.usage_by_date(limit=limit)


@router.get("/usage/weekly")
def usage_by_week(
    limit: int = 12,
    store: UsageStore = Depends(get_usage_store),
) -> list[dict]:
    return store.usage_by_week(limit=limit)


@router.get("/usage/monthly")
def usage_by_month(store: UsageStore = Depends(get_usage_store)) -> list[dict]:
    return store.usage_by_month()


@router.get("/usage/shifts")
def usage_by_shift(store: UsageStore = Depends(get_usage_store)) -> list[dict]:
    return store.usage_by_shift()


@router.get("/usage/day-of-week")
def usage_by_day_of_week(store: UsageStore = Depends(get_usage_store)) -> list[dict]:
    return store.usage_by_day_of_week()


@router.get("/burn-rate")
def burn_rate(store: UsageStore = Depends(get_usage_store)) -> dict:
    tokens_per_hour, cost_per_hour = store.burn_rate()
    input_tok, output_tok = store.total_tokens()
    return {
        "tokens_per_hour": tokens_per_hour,
        "cost_per_hour": cost_per_hour,
        "total_input_tokens": input_tok,
        "total_output_tokens": output_tok,
        "total_cost": store.total_cost(),
        "session_count": store.session_count(),
    }


@router.get("/projects")
def cost_by_project(
    limit: int = 20,
    store: UsageStore = Depends(get_usage_store),
) -> list[dict]:
    return store.projects_with_cost(limit=limit)


@router.get("/models")
def cost_by_model(store: UsageStore = Depends(get_usage_store)) -> list[dict]:
    return store.cost_by_model()
