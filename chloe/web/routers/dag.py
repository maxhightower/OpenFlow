"""DAG manipulation endpoints: tasks and edges within a project."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from chloe.graph.models import Task, TaskType, TaskStatus
from chloe.web.deps import get_project_service
from chloe.web.schemas import TaskCreate, TaskUpdate, EdgeCreate, EdgeDelete
from chloe.web.services.project_service import ProjectService

router = APIRouter(prefix="/api/projects/{project_id}/dag", tags=["dag"])


@router.get("")
def get_dag(
    project_id: str,
    svc: ProjectService = Depends(get_project_service),
) -> dict:
    try:
        engine = svc.get_engine(project_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Project not found")
    return engine.dag_engine.to_project_dag().to_dict()


@router.put("")
def replace_dag(
    project_id: str,
    dag_data: dict,
    svc: ProjectService = Depends(get_project_service),
) -> dict:
    project = svc.store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    svc.store.save_project_dag(project_id, json.dumps(dag_data))
    svc.invalidate(project_id)
    engine = svc.get_engine(project_id)
    return engine.dag_engine.to_project_dag().to_dict()


@router.get("/analysis")
def get_analysis(
    project_id: str,
    svc: ProjectService = Depends(get_project_service),
) -> dict:
    try:
        engine = svc.get_engine(project_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Project not found")
    dag = engine.dag_engine
    critical_path, critical_hours = dag.critical_path()
    levels = dag.topological_levels()
    tasks = dag.tasks
    done = sum(1 for t in tasks.values() if t.status == TaskStatus.DONE)
    return {
        "total_tasks": len(tasks),
        "done": done,
        "pending": sum(1 for t in tasks.values() if t.status == TaskStatus.PENDING),
        "blocked": sum(1 for t in tasks.values() if t.status == TaskStatus.BLOCKED),
        "in_progress": sum(1 for t in tasks.values() if t.status == TaskStatus.IN_PROGRESS),
        "critical_path": critical_path,
        "critical_path_hours": critical_hours,
        "levels": levels,
        "level_count": len(levels),
        "edge_count": engine.dag_engine.graph.number_of_edges(),
        "progress_pct": round(done / len(tasks) * 100, 1) if tasks else 0.0,
    }


@router.post("/tasks", status_code=201)
def add_task(
    project_id: str,
    body: TaskCreate,
    svc: ProjectService = Depends(get_project_service),
) -> dict:
    try:
        engine = svc.get_engine(project_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Project not found")

    task_id = body.id or f"T{len(engine.dag_engine.tasks) + 1}"
    if task_id in engine.dag_engine.tasks:
        raise HTTPException(status_code=409, detail=f"Task {task_id} already exists")

    task = Task(
        id=task_id,
        name=body.name,
        task_type=TaskType(body.task_type),
        estimated_hours=body.estimated_hours,
        status=TaskStatus.PENDING,
        priority=body.priority,
        estimated_tokens=body.estimated_tokens,
    )
    engine.dag_engine.add_task(task)

    for dep in body.depends_on:
        try:
            engine.dag_engine.add_dependency(dep, task_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    engine.dag_engine.update_blocked_statuses()
    svc.save_dag(project_id, engine)
    return task.to_dict()


@router.put("/tasks/{task_id}")
def update_task(
    project_id: str,
    task_id: str,
    body: TaskUpdate,
    svc: ProjectService = Depends(get_project_service),
) -> dict:
    try:
        engine = svc.get_engine(project_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        task = engine.dag_engine.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if body.name is not None:
        task.name = body.name
    if body.task_type is not None:
        task.task_type = TaskType(body.task_type)
    if body.estimated_hours is not None:
        task.estimated_hours = body.estimated_hours
    if body.priority is not None:
        task.priority = body.priority
    if body.estimated_tokens is not None:
        task.estimated_tokens = body.estimated_tokens
    if body.status is not None:
        task.status = TaskStatus(body.status)

    engine.dag_engine.update_blocked_statuses()
    svc.save_dag(project_id, engine)
    return task.to_dict()


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(
    project_id: str,
    task_id: str,
    svc: ProjectService = Depends(get_project_service),
) -> None:
    try:
        engine = svc.get_engine(project_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Project not found")

    if task_id not in engine.dag_engine.tasks:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    engine.dag_engine.graph.remove_node(task_id)
    del engine.dag_engine._tasks[task_id]
    svc.save_dag(project_id, engine)


@router.post("/edges", status_code=201)
def add_edge(
    project_id: str,
    body: EdgeCreate,
    svc: ProjectService = Depends(get_project_service),
) -> dict:
    try:
        engine = svc.get_engine(project_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        engine.dag_engine.add_dependency(body.from_id, body.to_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if engine.dag_engine.has_cycle():
        engine.dag_engine.graph.remove_edge(body.from_id, body.to_id)
        raise HTTPException(status_code=400, detail="Adding this edge would create a cycle")

    engine.dag_engine.update_blocked_statuses()
    svc.save_dag(project_id, engine)
    return {"from_id": body.from_id, "to_id": body.to_id}


@router.delete("/edges", status_code=204)
def delete_edge(
    project_id: str,
    body: EdgeDelete,
    svc: ProjectService = Depends(get_project_service),
) -> None:
    try:
        engine = svc.get_engine(project_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Project not found")

    if not engine.dag_engine.graph.has_edge(body.from_id, body.to_id):
        raise HTTPException(status_code=404, detail="Edge not found")

    engine.dag_engine.graph.remove_edge(body.from_id, body.to_id)
    engine.dag_engine.update_blocked_statuses()
    svc.save_dag(project_id, engine)
