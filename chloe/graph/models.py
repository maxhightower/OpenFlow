"""Task and dependency data models for the DAG engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class TaskType(str, Enum):
    BUG_FIX = "bug-fix"
    FEATURE = "feature"
    REFACTOR = "refactor"
    TEST = "test"
    DOCS = "docs"
    RELEASE = "release"


class TaskStatus(str, Enum):
    DONE = "done"
    IN_PROGRESS = "in-progress"
    PENDING = "pending"
    BLOCKED = "blocked"


# Display config per type
TYPE_STYLES: dict[TaskType, tuple[str, str]] = {
    TaskType.BUG_FIX: ("red", "B"),
    TaskType.FEATURE: ("green", "F"),
    TaskType.REFACTOR: ("yellow", "R"),
    TaskType.TEST: ("cyan", "T"),
    TaskType.DOCS: ("blue", "D"),
    TaskType.RELEASE: ("magenta", "X"),
}

STATUS_ICONS: dict[TaskStatus, str] = {
    TaskStatus.DONE: "[bright_green]\u2713[/bright_green]",
    TaskStatus.IN_PROGRESS: "[yellow]\u25b6[/yellow]",
    TaskStatus.PENDING: "[dim]\u25cb[/dim]",
    TaskStatus.BLOCKED: "[red]\u2718[/red]",
}


@dataclass
class Task:
    id: str
    name: str
    task_type: TaskType = TaskType.FEATURE
    estimated_hours: float = 1.0
    status: TaskStatus = TaskStatus.PENDING

    # Token budget fields
    estimated_tokens: int = 0
    actual_tokens: int = 0
    token_budget: int = 0          # Hard cap: skip if window has less than this

    # Scheduling metadata
    priority: int = 5              # 1 (highest) to 10 (lowest)
    agent_config_id: str | None = None
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    run_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "task_type": self.task_type.value,
            "estimated_hours": self.estimated_hours,
            "status": self.status.value,
            "estimated_tokens": self.estimated_tokens,
            "actual_tokens": self.actual_tokens,
            "token_budget": self.token_budget,
            "priority": self.priority,
            "agent_config_id": self.agent_config_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        return cls(
            id=data["id"],
            name=data["name"],
            task_type=TaskType(data.get("task_type", "feature")),
            estimated_hours=data.get("estimated_hours", 1.0),
            status=TaskStatus(data.get("status", "pending")),
            estimated_tokens=data.get("estimated_tokens", 0),
            actual_tokens=data.get("actual_tokens", 0),
            token_budget=data.get("token_budget", 0),
            priority=data.get("priority", 5),
            agent_config_id=data.get("agent_config_id"),
        )


@dataclass
class ProjectDAG:
    name: str
    tasks: list[Task] = field(default_factory=list)
    dependencies: list[tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "tasks": [t.to_dict() for t in self.tasks],
            "dependencies": [[a, b] for a, b in self.dependencies],
        }

    @classmethod
    def from_dict(cls, data: dict) -> ProjectDAG:
        return cls(
            name=data["name"],
            tasks=[Task.from_dict(t) for t in data.get("tasks", [])],
            dependencies=[tuple(d) for d in data.get("dependencies", [])],
        )
