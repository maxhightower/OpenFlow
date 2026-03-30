"""Cross-project dependency endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from chloe.web.deps import get_store
from chloe.web.schemas import CrossDepCreate
from chloe.web.store import WebStore

router = APIRouter(prefix="/api/cross-deps", tags=["cross-deps"])


@router.get("")
def list_cross_deps(store: WebStore = Depends(get_store)) -> list[dict]:
    return store.list_cross_deps()


@router.post("", status_code=201)
def add_cross_dep(
    body: CrossDepCreate,
    store: WebStore = Depends(get_store),
) -> dict:
    dep_id = store.add_cross_dep(
        from_project_id=body.from_project_id,
        from_task_id=body.from_task_id,
        to_project_id=body.to_project_id,
        to_task_id=body.to_task_id,
    )
    return {"id": dep_id, **body.model_dump()}


@router.delete("/{dep_id}", status_code=204)
def delete_cross_dep(
    dep_id: int,
    store: WebStore = Depends(get_store),
) -> None:
    if not store.delete_cross_dep(dep_id):
        raise HTTPException(status_code=404, detail="Dependency not found")
