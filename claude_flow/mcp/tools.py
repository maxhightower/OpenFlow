"""MCP tool handler implementations for ClaudeFlow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claude_flow.scheduler.engine import SchedulerEngine


class DAGPersistence:
    """Saves the current DAG state back to a JSON file after mutations."""

    def __init__(self, file_path: Path | None) -> None:
        self.file_path = file_path

    def save(self, engine: SchedulerEngine) -> None:
        if self.file_path is None:
            return
        dag = engine.dag_engine.to_project_dag()
        with open(self.file_path, "w") as fh:
            json.dump(dag.to_dict(), fh, indent=2)


class ClaudeFlowTools:
    """
    Implements the 8 MCP tools exposed to Claude Code.
    Each method returns a plain dict that gets serialised to JSON text content.
    """

    def __init__(
        self,
        engine: SchedulerEngine,
        dag_persistence: DAGPersistence | None = None,
    ) -> None:
        self.engine = engine
        self._persistence = dag_persistence

    def _auto_save(self) -> None:
        """Persist the DAG to disk if a file path was provided."""
        if self._persistence is not None:
            self._persistence.save(self.engine)

    # ------------------------------------------------------------------
    # 1. get_budget_status
    # ------------------------------------------------------------------

    async def get_budget_status(self, _args: dict) -> dict:
        """Return the current token budget window state."""
        summary = self.engine.window_summary()
        runnable = self.engine._get_runnable_tasks()
        avg_cost = 0
        if runnable:
            estimates = self.engine.estimator.estimate_tokens_for_schedule(
                runnable, self.engine.model
            )
            avg_cost = sum(estimates.values()) // max(len(estimates), 1)

        tasks_remaining = (
            summary["tokens_remaining"] // avg_cost if avg_cost > 0 else "unknown"
        )

        return {
            "window_id": summary["window_id"],
            "tokens_used": summary["tokens_used"],
            "tokens_remaining": summary["tokens_remaining"],
            "token_budget": summary["token_budget"],
            "pct_used": summary["pct_tokens_used"],
            "elapsed_hours": summary["elapsed_hours"],
            "remaining_hours": summary["remaining_hours"],
            "total_cost_usd": summary["total_cost_usd"],
            "estimated_tasks_remaining": tasks_remaining,
            "is_active": summary["is_active"],
        }

    # ------------------------------------------------------------------
    # 2. get_schedule
    # ------------------------------------------------------------------

    async def get_schedule(self, args: dict) -> dict:
        """Return the current optimized execution plan."""
        plan = self.engine.build_schedule()
        tasks_out = []
        for st in plan.ordered_tasks:
            task = self.engine.dag_engine.get_task(st.task_id)
            tasks_out.append({
                "id": task.id,
                "name": task.name,
                "type": task.task_type.value,
                "priority": task.priority,
                "priority_rank": st.priority_rank,
                "estimated_tokens": st.estimated_token_cost,
                "status": task.status.value,
                "scheduled_start": st.scheduled_start.isoformat(),
            })

        return {
            "dag_name": plan.dag_name,
            "tasks": tasks_out,
            "total_estimated_tokens": plan.total_estimated_tokens,
            "total_estimated_cost_usd": plan.total_estimated_cost_usd,
            "is_feasible": plan.is_feasible,
            "infeasibility_reason": plan.infeasibility_reason,
            "window_tokens_remaining": self.engine.current_window().tokens_remaining,
        }

    # ------------------------------------------------------------------
    # 3. get_next_task
    # ------------------------------------------------------------------

    async def get_next_task(self, _args: dict) -> dict:
        """Return the next task that would be executed."""
        task = self.engine.next_task()
        if task is None:
            return {"task": None, "reason": "No runnable tasks in current budget window"}

        estimates = self.engine.estimator.estimate_tokens_for_schedule(
            [task], self.engine.model
        )
        return {
            "task": {
                "id": task.id,
                "name": task.name,
                "type": task.task_type.value,
                "priority": task.priority,
                "estimated_tokens": estimates.get(task.id, 0),
                "status": task.status.value,
                "agent_config_id": task.agent_config_id,
            }
        }

    # ------------------------------------------------------------------
    # 4. queue_task
    # ------------------------------------------------------------------

    async def queue_task(self, args: dict) -> dict:
        """Add a new task to the in-memory DAG."""
        from claude_flow.graph.models import Task, TaskType, TaskStatus

        task_id = args.get("id") or f"T{args['name'][:4].upper().replace(' ', '')}"
        task = Task(
            id=task_id,
            name=args["name"],
            task_type=TaskType(args.get("task_type", "feature")),
            estimated_hours=args.get("estimated_hours", 1.0),
            status=TaskStatus.PENDING,
            priority=args.get("priority", 5),
            estimated_tokens=args.get("estimated_tokens", 0),
        )

        self.engine.dag_engine.add_task(task)

        for dep in args.get("depends_on", []):
            try:
                self.engine.dag_engine.add_dependency(dep, task_id)
            except ValueError as e:
                return {"error": str(e)}

        self.engine.dag_engine.update_blocked_statuses()

        plan = self.engine.build_schedule()
        position = next(
            (st.priority_rank for st in plan.ordered_tasks if st.task_id == task_id),
            None,
        )

        self._auto_save()

        return {
            "task_id": task_id,
            "name": task.name,
            "status": task.status.value,
            "position_in_queue": position,
            "message": f"Task {task_id} added to DAG",
        }

    # ------------------------------------------------------------------
    # 5. run_next_task
    # ------------------------------------------------------------------

    async def run_next_task(self, args: dict) -> dict:
        """Execute the next scheduled task via the claude CLI."""
        import asyncio
        from claude_flow.scheduler.models import AgentConfig
        from claude_flow.dispatcher.runner import TaskRunner, TaskRunError

        dry_run = args.get("dry_run", False)
        task = self.engine.next_task()
        if task is None:
            return {"status": "skipped", "reason": "No runnable tasks"}

        config = AgentConfig(
            config_id="_mcp_default",
            prompt_template=(
                "Complete the following task:\n\n"
                "Task: {{ task.name }}\n"
                "Type: {{ task.task_type.value }}\n"
                "Estimated effort: {{ task.estimated_hours }}h\n"
            ),
            working_directory=str(__import__("pathlib").Path.cwd()),
            model=self.engine.model,
        )

        window = self.engine.current_window()
        runner = TaskRunner(store=self.engine.store)

        if dry_run:
            args_list = runner.build_cli_args(task, config)
            return {
                "status": "dry_run",
                "task_id": task.id,
                "task_name": task.name,
                "command": " ".join(args_list[:6]) + " ...",
            }

        try:
            task_run = await runner.run(task, config, window.window_id, dry_run=False)
            self.engine.mark_completed(task.id, task_run)
            self._auto_save()
            return {
                "status": "completed",
                "run_id": task_run.run_id,
                "task_id": task.id,
                "actual_tokens": task_run.actual_total_tokens,
                "cost_usd": task_run.actual_cost_usd,
            }
        except TaskRunError as e:
            return {
                "status": "failed",
                "run_id": e.run.run_id,
                "task_id": task.id,
                "error": str(e),
            }

    # ------------------------------------------------------------------
    # 6. mark_task_done
    # ------------------------------------------------------------------

    async def mark_task_done(self, args: dict) -> dict:
        """Manually mark a task as completed (no subprocess)."""
        import uuid
        from datetime import datetime, timezone
        from claude_flow.scheduler.models import TaskRun, RunStatus

        task_id = args["task_id"]
        actual_tokens = args.get("actual_tokens", 0)

        try:
            task = self.engine.dag_engine.get_task(task_id)
        except KeyError:
            return {"ok": False, "error": f"Unknown task: {task_id}"}

        now = datetime.now(timezone.utc)
        window = self.engine.current_window()
        run = TaskRun(
            run_id=str(uuid.uuid4()),
            task_id=task_id,
            window_id=window.window_id,
            model=self.engine.model,
            started_at=now,
            completed_at=now,
            exit_code=0,
            actual_input_tokens=actual_tokens,
            actual_output_tokens=0,
            actual_cost_usd=0.0,
            status=RunStatus.COMPLETED,
        )
        self.engine.mark_completed(task_id, run)

        self._auto_save()

        return {
            "ok": True,
            "task_id": task_id,
            "new_status": task.status.value,
            "message": f"Task {task_id} marked as done",
        }

    # ------------------------------------------------------------------
    # 7. get_task_status
    # ------------------------------------------------------------------

    async def get_task_status(self, args: dict) -> dict:
        """Return status and run info for a specific task."""
        task_id = args["task_id"]
        try:
            task = self.engine.dag_engine.get_task(task_id)
        except KeyError:
            return {"error": f"Unknown task: {task_id}"}

        runs = self.engine.store.get_runs_for_task(task_id)
        latest = runs[0] if runs else None

        return {
            "task_id": task.id,
            "name": task.name,
            "status": task.status.value,
            "priority": task.priority,
            "estimated_tokens": task.estimated_tokens,
            "actual_tokens": task.actual_tokens,
            "run_id": latest.run_id if latest else None,
            "started_at": latest.started_at.isoformat() if latest else None,
            "completed_at": latest.completed_at.isoformat() if latest and latest.completed_at else None,
            "run_status": latest.status.value if latest else None,
        }

    # ------------------------------------------------------------------
    # 8. list_runs
    # ------------------------------------------------------------------

    async def list_runs(self, args: dict) -> dict:
        """Return recent task run history."""
        limit = args.get("limit", 20)
        window_id = args.get("window_id")

        if window_id:
            runs = self.engine.store.get_runs_in_window(window_id)
        else:
            runs = self.engine.store.get_recent_runs(limit=limit)

        return {
            "runs": [
                {
                    "run_id": r.run_id,
                    "task_id": r.task_id,
                    "status": r.status.value,
                    "actual_tokens": r.actual_total_tokens,
                    "cost_usd": r.actual_cost_usd,
                    "model": r.model,
                    "started_at": r.started_at.isoformat(),
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                }
                for r in runs[:limit]
            ],
            "count": len(runs[:limit]),
        }
