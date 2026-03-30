"""Scheduler data models: budget windows, schedules, task runs, agent configs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum


class WarningLevel(str, Enum):
    OK = "ok"
    CAUTION = "caution"    # >60% used or <90 min remaining
    WARNING = "warning"    # >80% used or <45 min remaining
    CRITICAL = "critical"  # >90% used or <15 min remaining
    UNKNOWN = "unknown"    # no cap inferred yet


WINDOW_DURATION_SECONDS = 5 * 60 * 60  # 5 hours


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AgentConfig:
    """Describes how to invoke the claude CLI for a task."""

    config_id: str
    prompt_template: str
    working_directory: str
    model: str = "claude-sonnet-4-6"
    max_turns: int | None = None
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    extra_flags: list[str] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "config_id": self.config_id,
            "prompt_template": self.prompt_template,
            "working_directory": self.working_directory,
            "model": self.model,
            "max_turns": self.max_turns,
            "allowed_tools": self.allowed_tools,
            "disallowed_tools": self.disallowed_tools,
            "extra_flags": self.extra_flags,
            "env_vars": self.env_vars,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentConfig:
        return cls(
            config_id=data["config_id"],
            prompt_template=data["prompt_template"],
            working_directory=data["working_directory"],
            model=data.get("model", "claude-sonnet-4-6"),
            max_turns=data.get("max_turns"),
            allowed_tools=data.get("allowed_tools", []),
            disallowed_tools=data.get("disallowed_tools", []),
            extra_flags=data.get("extra_flags", []),
            env_vars=data.get("env_vars", {}),
        )


@dataclass
class BudgetWindow:
    """An operator-defined token budget window (default: 5 hours)."""

    window_id: str
    started_at: datetime
    token_budget: int               # Total tokens allowed in this window
    total_tokens_used: int = 0
    total_cost_usd: float = 0.0

    @property
    def ends_at(self) -> datetime:
        return self.started_at + timedelta(seconds=WINDOW_DURATION_SECONDS)

    @property
    def elapsed_seconds(self) -> float:
        now = datetime.now(timezone.utc)
        return max(0.0, (now - self.started_at).total_seconds())

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, WINDOW_DURATION_SECONDS - self.elapsed_seconds)

    @property
    def is_active(self) -> bool:
        return self.remaining_seconds > 0

    @property
    def tokens_remaining(self) -> int:
        return max(0, self.token_budget - self.total_tokens_used)

    @property
    def utilization_fraction(self) -> float:
        if self.token_budget == 0:
            return 0.0
        return min(1.0, self.total_tokens_used / self.token_budget)

    def to_dict(self) -> dict:
        return {
            "window_id": self.window_id,
            "started_at": self.started_at.isoformat(),
            "token_budget": self.token_budget,
            "total_tokens_used": self.total_tokens_used,
            "total_cost_usd": self.total_cost_usd,
        }


@dataclass
class ScheduledTask:
    """One entry in an execution plan."""

    task_id: str
    scheduled_start: datetime
    estimated_token_cost: int
    priority_rank: int              # 1 = run first
    window_id: str
    can_run_parallel_with: list[str] = field(default_factory=list)
    slack_seconds: float = 0.0


@dataclass
class Schedule:
    """A full execution plan for a DAG."""

    schedule_id: str
    dag_name: str
    created_at: datetime
    window: BudgetWindow
    ordered_tasks: list[ScheduledTask]
    total_estimated_tokens: int
    total_estimated_cost_usd: float
    estimated_completion: datetime
    is_feasible: bool = True
    infeasibility_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "schedule_id": self.schedule_id,
            "dag_name": self.dag_name,
            "created_at": self.created_at.isoformat(),
            "window": self.window.to_dict(),
            "ordered_tasks": [
                {
                    "task_id": t.task_id,
                    "priority_rank": t.priority_rank,
                    "estimated_token_cost": t.estimated_token_cost,
                    "scheduled_start": t.scheduled_start.isoformat(),
                }
                for t in self.ordered_tasks
            ],
            "total_estimated_tokens": self.total_estimated_tokens,
            "total_estimated_cost_usd": self.total_estimated_cost_usd,
            "estimated_completion": self.estimated_completion.isoformat(),
            "is_feasible": self.is_feasible,
            "infeasibility_reason": self.infeasibility_reason,
        }


@dataclass
class TaskRun:
    """A record of one execution attempt of a task."""

    run_id: str
    task_id: str
    window_id: str
    model: str
    started_at: datetime
    completed_at: datetime | None = None
    exit_code: int | None = None
    actual_input_tokens: int = 0
    actual_output_tokens: int = 0
    actual_cost_usd: float = 0.0
    stdout_path: str | None = None
    stderr_path: str | None = None
    status: RunStatus = RunStatus.RUNNING

    @property
    def actual_total_tokens(self) -> int:
        return self.actual_input_tokens + self.actual_output_tokens

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "window_id": self.window_id,
            "model": self.model,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "exit_code": self.exit_code,
            "actual_input_tokens": self.actual_input_tokens,
            "actual_output_tokens": self.actual_output_tokens,
            "actual_cost_usd": self.actual_cost_usd,
            "status": self.status.value,
        }


@dataclass
class RateLimitEvent:
    """A recorded rate-limit hit — the primary signal for cap inference."""

    event_id: str
    occurred_at: datetime
    tokens_consumed: int       # tokens counted in the window when the limit hit
    retry_after_seconds: int   # from the API error (0 if unknown)
    window_start_estimate: datetime  # back-calculated: occurred_at - 5h + retry_after

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "occurred_at": self.occurred_at.isoformat(),
            "tokens_consumed": self.tokens_consumed,
            "retry_after_seconds": self.retry_after_seconds,
            "window_start_estimate": self.window_start_estimate.isoformat(),
        }


@dataclass
class WindowEstimate:
    """Best-effort estimate of the current 5-hour subscription window state."""

    inferred_cap: int | None        # None until >= 1 rate-limit observation
    tokens_used: int
    tokens_remaining: int | None    # None if cap unknown
    fraction_used: float | None     # None if cap unknown
    tokens_per_hour: float          # current burn rate
    minutes_to_limit: float | None  # None if cap unknown or burn rate == 0
    warning_level: WarningLevel
    sample_count: int               # rate-limit events that informed the cap

    def to_dict(self) -> dict:
        return {
            "inferred_cap": self.inferred_cap,
            "tokens_used": self.tokens_used,
            "tokens_remaining": self.tokens_remaining,
            "fraction_used": self.fraction_used,
            "tokens_per_hour": self.tokens_per_hour,
            "minutes_to_limit": self.minutes_to_limit,
            "warning_level": self.warning_level.value,
            "sample_count": self.sample_count,
        }


@dataclass
class CostEstimate:
    """Learned token cost distribution for a task type + model combination."""

    task_type: str
    model: str
    p50_tokens: int
    p90_tokens: int
    sample_count: int
    last_updated: datetime

    def to_dict(self) -> dict:
        return {
            "task_type": self.task_type,
            "model": self.model,
            "p50_tokens": self.p50_tokens,
            "p90_tokens": self.p90_tokens,
            "sample_count": self.sample_count,
            "last_updated": self.last_updated.isoformat(),
        }
