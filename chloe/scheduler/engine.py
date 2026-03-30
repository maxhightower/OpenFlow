"""SchedulerEngine: central coordinator for budget-aware task scheduling."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from chloe.graph.engine import DAGEngine
from chloe.graph.models import Task, TaskStatus
from chloe.scheduler.estimator import CostEstimator
from chloe.scheduler.models import (
    BudgetWindow,
    Schedule,
    ScheduledTask,
    TaskRun,
    WINDOW_DURATION_SECONDS,
)
from chloe.scheduler.optimizer import BudgetOptimizer
from chloe.scheduler.store import SchedulerStore

# Gap in seconds between tasks that signals a new window has started
NEW_WINDOW_IDLE_GAP = 10 * 60  # 10 minutes

DEFAULT_TOKEN_BUDGET = 500_000


class SchedulerEngine:
    def __init__(
        self,
        dag_engine: DAGEngine,
        store: SchedulerStore,
        estimator: CostEstimator,
        optimizer: BudgetOptimizer,
        token_budget_per_window: int = DEFAULT_TOKEN_BUDGET,
        model: str = "claude-sonnet-4-6",
    ) -> None:
        self.dag_engine = dag_engine
        self.store = store
        self.estimator = estimator
        self.optimizer = optimizer
        self.token_budget_per_window = token_budget_per_window
        self.model = model

    @classmethod
    def from_dag_engine(
        cls,
        dag_engine: DAGEngine,
        db_path: Path | None = None,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        model: str = "claude-sonnet-4-6",
    ) -> SchedulerEngine:
        store = SchedulerStore(db_path)
        estimator = CostEstimator(store)
        optimizer = BudgetOptimizer()
        return cls(dag_engine, store, estimator, optimizer, token_budget, model)

    # -- Window management -----------------------------------------------------

    def current_window(self) -> BudgetWindow:
        """
        Return the active BudgetWindow, creating a new one if needed.

        A new window is created when:
        - No window exists yet
        - The most recent window has expired (> 5 hours old)
        """
        window = self.store.get_active_window()
        if window is not None:
            # Sync actual token usage from completed runs
            used = self.store.tokens_used_in_window(window.window_id)
            window.total_tokens_used = used
            return window

        # Create a new window
        window = BudgetWindow(
            window_id=str(uuid.uuid4()),
            started_at=datetime.now(timezone.utc),
            token_budget=self.token_budget_per_window,
        )
        self.store.save_window(window)
        return window

    def window_summary(self) -> dict:
        """Return a display-ready dict of the current window state."""
        window = self.current_window()
        elapsed_h = window.elapsed_seconds / 3600
        remaining_h = window.remaining_seconds / 3600
        pct_used = round(window.utilization_fraction * 100, 1)
        pct_time = round((window.elapsed_seconds / WINDOW_DURATION_SECONDS) * 100, 1)

        return {
            "window_id": window.window_id,
            "started_at": window.started_at.isoformat(),
            "ends_at": window.ends_at.isoformat(),
            "token_budget": window.token_budget,
            "tokens_used": window.total_tokens_used,
            "tokens_remaining": window.tokens_remaining,
            "pct_tokens_used": pct_used,
            "elapsed_hours": round(elapsed_h, 2),
            "remaining_hours": round(remaining_h, 2),
            "pct_time_elapsed": pct_time,
            "total_cost_usd": window.total_cost_usd,
            "is_active": window.is_active,
        }

    # -- Scheduling ------------------------------------------------------------

    def build_schedule(self) -> Schedule:
        """
        Produce an optimized execution plan for the current DAG + budget window.

        Steps:
        1. Collect runnable tasks (PENDING with all predecessors DONE)
        2. Estimate token costs
        3. Run OR-Tools optimizer
        4. Persist and return Schedule
        """
        window = self.current_window()
        now = datetime.now(timezone.utc)

        # Only schedule PENDING tasks whose predecessors are all DONE
        self.dag_engine.update_blocked_statuses()
        runnable = self._get_runnable_tasks()

        if not runnable:
            return Schedule(
                schedule_id=str(uuid.uuid4()),
                dag_name=self.dag_engine.dag.name if self.dag_engine.dag else "unknown",
                created_at=now,
                window=window,
                ordered_tasks=[],
                total_estimated_tokens=0,
                total_estimated_cost_usd=0.0,
                estimated_completion=now,
                is_feasible=True,
                infeasibility_reason="No runnable tasks",
            )

        token_estimates = self.estimator.estimate_tokens_for_schedule(runnable, self.model)
        dependencies = list(self.dag_engine.graph.edges())

        scheduled_tasks = self.optimizer.solve(
            tasks=runnable,
            token_estimates=token_estimates,
            dependencies=dependencies,
            window=window,
        )

        total_tokens = sum(st.estimated_token_cost for st in scheduled_tasks)
        is_feasible = len(scheduled_tasks) > 0 or len(runnable) == 0
        reason = None
        if not scheduled_tasks and runnable:
            reason = f"Insufficient budget: {window.tokens_remaining:,} tokens remaining, need at least {min(token_estimates.values()):,}"

        estimated_completion = (
            scheduled_tasks[-1].scheduled_start + timedelta(
                seconds=max(1, (scheduled_tasks[-1].estimated_token_cost * 10) // 1000)
            )
            if scheduled_tasks else now
        )

        schedule = Schedule(
            schedule_id=str(uuid.uuid4()),
            dag_name=self.dag_engine.dag.name if self.dag_engine.dag else "unknown",
            created_at=now,
            window=window,
            ordered_tasks=scheduled_tasks,
            total_estimated_tokens=total_tokens,
            total_estimated_cost_usd=self._estimate_cost(total_tokens),
            estimated_completion=estimated_completion,
            is_feasible=is_feasible,
            infeasibility_reason=reason,
        )

        self.store.save_schedule(schedule)
        return schedule

    def next_task(self) -> Task | None:
        """Return the highest-priority runnable task that fits in the current budget."""
        schedule = self.build_schedule()
        if not schedule.ordered_tasks:
            return None
        return self.dag_engine.get_task(schedule.ordered_tasks[0].task_id)

    def mark_completed(self, task_id: str, run: TaskRun) -> None:
        """Record completion, update task status, trigger reconciliation."""
        task = self.dag_engine.get_task(task_id)
        task.status = TaskStatus.DONE
        task.actual_tokens = run.actual_total_tokens
        task.completed_at = run.completed_at
        task.run_id = run.run_id
        self.dag_engine.update_blocked_statuses()

        # Update window usage
        window = self.current_window()
        window.total_tokens_used = self.store.tokens_used_in_window(window.window_id)
        window.total_cost_usd += run.actual_cost_usd
        self.store.save_window(window)

        self.store.save_run(run)
        self.estimator.update(task, run)

    # -- Helpers ---------------------------------------------------------------

    def _get_runnable_tasks(self) -> list[Task]:
        """Tasks that are PENDING with all predecessors DONE."""
        runnable = []
        for task_id, task in self.dag_engine.tasks.items():
            if task.status != TaskStatus.PENDING:
                continue
            predecessors = list(self.dag_engine.graph.predecessors(task_id))
            all_done = all(
                self.dag_engine.get_task(p).status == TaskStatus.DONE
                for p in predecessors
            )
            if all_done:
                runnable.append(task)
        return runnable

    def _estimate_cost(self, total_tokens: int) -> float:
        from chloe.observer.parser import COST_PER_INPUT_TOKEN, DEFAULT_COST_PER_INPUT
        rate = COST_PER_INPUT_TOKEN.get(self.model, DEFAULT_COST_PER_INPUT)
        return round(total_tokens * rate, 4)
