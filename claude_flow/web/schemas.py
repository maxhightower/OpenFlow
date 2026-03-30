"""Pydantic request/response models for the web API."""

from __future__ import annotations

from pydantic import BaseModel, Field


# -- Projects ------------------------------------------------------------------

class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    dag: dict = Field(default_factory=lambda: {"name": "Untitled", "tasks": [], "dependencies": []})


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class ProjectResponse(BaseModel):
    project_id: str
    name: str
    description: str
    dag: dict
    created_at: str
    updated_at: str
    is_archived: bool


# -- Tasks ---------------------------------------------------------------------

class TaskCreate(BaseModel):
    id: str | None = None
    name: str
    task_type: str = "feature"
    estimated_hours: float = 1.0
    priority: int = 5
    estimated_tokens: int = 0
    depends_on: list[str] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    name: str | None = None
    task_type: str | None = None
    estimated_hours: float | None = None
    priority: int | None = None
    estimated_tokens: int | None = None
    status: str | None = None


# -- Edges ---------------------------------------------------------------------

class EdgeCreate(BaseModel):
    from_id: str
    to_id: str


class EdgeDelete(BaseModel):
    from_id: str
    to_id: str


# -- Cross-project deps -------------------------------------------------------

class CrossDepCreate(BaseModel):
    from_project_id: str
    from_task_id: str
    to_project_id: str
    to_task_id: str


# -- Task execution ------------------------------------------------------------

class TaskRunRequest(BaseModel):
    dry_run: bool = False


class MarkDoneRequest(BaseModel):
    actual_tokens: int = 0
