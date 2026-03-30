"""Scheduler endpoints: schedule, optimize, run, mark done."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from claude_flow.web.deps import get_project_service
from claude_flow.web.schemas import TaskRunRequest, MarkDoneRequest
from claude_flow.web.services.project_service import ProjectService
from claude_flow.mcp.tools import ClaudeFlowTools

router = APIRouter(prefix="/api/projects/{project_id}", tags=["scheduler"])


def _tools_for(project_id: str, svc: ProjectService) -> ClaudeFlowTools:
    try:
        engine = svc.get_engine(project_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Project not found")
    return ClaudeFlowTools(engine)


@router.get("/schedule")
async def get_schedule(
    project_id: str,
    svc: ProjectService = Depends(get_project_service),
) -> dict:
    tools = _tools_for(project_id, svc)
    return await tools.get_schedule({})


@router.post("/schedule/optimize")
async def optimize_schedule(
    project_id: str,
    svc: ProjectService = Depends(get_project_service),
) -> dict:
    tools = _tools_for(project_id, svc)
    result = await tools.get_schedule({})
    svc.save_dag(project_id, tools.engine)
    return result


@router.get("/next-task")
async def get_next_task(
    project_id: str,
    svc: ProjectService = Depends(get_project_service),
) -> dict:
    tools = _tools_for(project_id, svc)
    return await tools.get_next_task({})


@router.post("/tasks/{task_id}/run")
async def run_task(
    project_id: str,
    task_id: str,
    body: TaskRunRequest = TaskRunRequest(),
    svc: ProjectService = Depends(get_project_service),
) -> dict:
    tools = _tools_for(project_id, svc)
    result = await tools.run_next_task({"dry_run": body.dry_run})
    svc.save_dag(project_id, tools.engine)
    return result


@router.post("/tasks/{task_id}/done")
async def mark_task_done(
    project_id: str,
    task_id: str,
    body: MarkDoneRequest = MarkDoneRequest(),
    svc: ProjectService = Depends(get_project_service),
) -> dict:
    tools = _tools_for(project_id, svc)
    result = await tools.mark_task_done({
        "task_id": task_id,
        "actual_tokens": body.actual_tokens,
    })
    svc.save_dag(project_id, tools.engine)
    return result


@router.get("/tasks/{task_id}/status")
async def get_task_status(
    project_id: str,
    task_id: str,
    svc: ProjectService = Depends(get_project_service),
) -> dict:
    tools = _tools_for(project_id, svc)
    return await tools.get_task_status({"task_id": task_id})
