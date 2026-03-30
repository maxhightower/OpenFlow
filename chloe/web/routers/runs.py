"""Task run history endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from chloe.web.deps import get_store
from chloe.web.store import WebStore

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("")
def list_runs(
    limit: int = 20,
    window_id: str | None = None,
    store: WebStore = Depends(get_store),
) -> dict:
    if window_id:
        runs = store.get_runs_in_window(window_id)
    else:
        runs = store.get_recent_runs(limit=limit)
    return {
        "runs": [r.to_dict() for r in runs[:limit]],
        "count": len(runs[:limit]),
    }


@router.get("/{run_id}")
def get_run(
    run_id: str,
    store: WebStore = Depends(get_store),
) -> dict:
    # Search across all runs
    rows = store._conn.execute(
        "SELECT * FROM task_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if rows is None:
        raise HTTPException(status_code=404, detail="Run not found")
    from chloe.scheduler.store import _row_to_run
    return _row_to_run(rows).to_dict()
