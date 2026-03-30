"""Project CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from claude_flow.web.deps import get_project_service
from claude_flow.web.schemas import ProjectCreate, ProjectUpdate
from claude_flow.web.services.project_service import ProjectService

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
def list_projects(
    include_archived: bool = False,
    svc: ProjectService = Depends(get_project_service),
) -> list[dict]:
    return svc.list_projects(include_archived=include_archived)


@router.post("", status_code=201)
def create_project(
    body: ProjectCreate,
    svc: ProjectService = Depends(get_project_service),
) -> dict:
    return svc.create_project(
        name=body.name,
        description=body.description,
        dag_dict=body.dag,
    )


@router.get("/{project_id}")
def get_project(
    project_id: str,
    svc: ProjectService = Depends(get_project_service),
) -> dict:
    project = svc.store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.put("/{project_id}")
def update_project(
    project_id: str,
    body: ProjectUpdate,
    svc: ProjectService = Depends(get_project_service),
) -> dict:
    existing = svc.store.get_project(project_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Project not found")
    updates = body.model_dump(exclude_none=True)
    result = svc.store.update_project(project_id, **updates)
    svc.invalidate(project_id)
    return result


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    svc: ProjectService = Depends(get_project_service),
) -> None:
    if not svc.store.delete_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    svc.invalidate(project_id)
