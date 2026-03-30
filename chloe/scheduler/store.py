"""SQLite persistence for scheduler state: windows, schedules, runs, estimates."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from chloe.scheduler.models import (
    BudgetWindow,
    CostEstimate,
    RateLimitEvent,
    RunStatus,
    ScheduledTask,
    Schedule,
    TaskRun,
)

DEFAULT_DB_PATH = Path.home() / ".chloe" / "scheduler.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS budget_windows (
    window_id     TEXT PRIMARY KEY,
    started_at    TEXT NOT NULL,
    token_budget  INTEGER NOT NULL,
    tokens_used   INTEGER NOT NULL DEFAULT 0,
    total_cost    REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS schedules (
    schedule_id   TEXT PRIMARY KEY,
    dag_name      TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    window_id     TEXT NOT NULL,
    is_feasible   INTEGER NOT NULL DEFAULT 1,
    reason        TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id         TEXT NOT NULL,
    task_id             TEXT NOT NULL,
    scheduled_start     TEXT NOT NULL,
    estimated_tokens    INTEGER NOT NULL DEFAULT 0,
    priority_rank       INTEGER NOT NULL DEFAULT 0,
    window_id           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_runs (
    run_id                TEXT PRIMARY KEY,
    task_id               TEXT NOT NULL,
    window_id             TEXT NOT NULL,
    model                 TEXT NOT NULL DEFAULT 'unknown',
    started_at            TEXT NOT NULL,
    completed_at          TEXT,
    exit_code             INTEGER,
    actual_input_tokens   INTEGER NOT NULL DEFAULT 0,
    actual_output_tokens  INTEGER NOT NULL DEFAULT 0,
    actual_cost_usd       REAL NOT NULL DEFAULT 0.0,
    stdout_path           TEXT,
    stderr_path           TEXT,
    status                TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS cost_estimates (
    task_type     TEXT NOT NULL,
    model         TEXT NOT NULL,
    p50_tokens    INTEGER NOT NULL DEFAULT 0,
    p90_tokens    INTEGER NOT NULL DEFAULT 0,
    sample_count  INTEGER NOT NULL DEFAULT 0,
    last_updated  TEXT NOT NULL,
    PRIMARY KEY (task_type, model)
);

CREATE TABLE IF NOT EXISTS rate_limit_events (
    event_id              TEXT PRIMARY KEY,
    occurred_at           TEXT NOT NULL,
    tokens_consumed       INTEGER NOT NULL,
    retry_after_seconds   INTEGER NOT NULL DEFAULT 0,
    window_start_estimate TEXT NOT NULL
);
"""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class SchedulerStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- BudgetWindow ----------------------------------------------------------

    def save_window(self, window: BudgetWindow) -> None:
        self._conn.execute(
            """
            INSERT INTO budget_windows (window_id, started_at, token_budget, tokens_used, total_cost)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(window_id) DO UPDATE SET
                tokens_used  = excluded.tokens_used,
                total_cost   = excluded.total_cost
            """,
            (
                window.window_id,
                window.started_at.isoformat(),
                window.token_budget,
                window.total_tokens_used,
                window.total_cost_usd,
            ),
        )
        self._conn.commit()

    def get_active_window(self) -> BudgetWindow | None:
        """Return the most recent window that hasn't expired yet."""
        row = self._conn.execute(
            "SELECT * FROM budget_windows ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        window = BudgetWindow(
            window_id=row["window_id"],
            started_at=_parse_dt(row["started_at"]),
            token_budget=row["token_budget"],
            total_tokens_used=row["tokens_used"],
            total_cost_usd=row["total_cost"],
        )
        return window if window.is_active else None

    def get_window(self, window_id: str) -> BudgetWindow | None:
        row = self._conn.execute(
            "SELECT * FROM budget_windows WHERE window_id = ?", (window_id,)
        ).fetchone()
        if row is None:
            return None
        return BudgetWindow(
            window_id=row["window_id"],
            started_at=_parse_dt(row["started_at"]),
            token_budget=row["token_budget"],
            total_tokens_used=row["tokens_used"],
            total_cost_usd=row["total_cost"],
        )

    def tokens_used_in_window(self, window_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(actual_input_tokens + actual_output_tokens), 0) AS total "
            "FROM task_runs WHERE window_id = ? AND status = 'completed'",
            (window_id,),
        ).fetchone()
        return row["total"] if row else 0

    # -- Schedule --------------------------------------------------------------

    def save_schedule(self, schedule: Schedule) -> None:
        self._conn.execute(
            """
            INSERT INTO schedules (schedule_id, dag_name, created_at, window_id, is_feasible, reason, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(schedule_id) DO NOTHING
            """,
            (
                schedule.schedule_id,
                schedule.dag_name,
                schedule.created_at.isoformat(),
                schedule.window.window_id,
                int(schedule.is_feasible),
                schedule.infeasibility_reason,
                json.dumps({"total_tokens": schedule.total_estimated_tokens}),
            ),
        )
        for st in schedule.ordered_tasks:
            self._conn.execute(
                """
                INSERT INTO scheduled_tasks (schedule_id, task_id, scheduled_start, estimated_tokens, priority_rank, window_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    schedule.schedule_id,
                    st.task_id,
                    st.scheduled_start.isoformat(),
                    st.estimated_token_cost,
                    st.priority_rank,
                    st.window_id,
                ),
            )
        self._conn.commit()

    def get_latest_schedule(self, dag_name: str) -> Schedule | None:
        row = self._conn.execute(
            "SELECT * FROM schedules WHERE dag_name = ? ORDER BY created_at DESC LIMIT 1",
            (dag_name,),
        ).fetchone()
        if row is None:
            return None
        window = self.get_window(row["window_id"])
        if window is None:
            return None
        task_rows = self._conn.execute(
            "SELECT * FROM scheduled_tasks WHERE schedule_id = ? ORDER BY priority_rank",
            (row["schedule_id"],),
        ).fetchall()
        ordered_tasks = [
            ScheduledTask(
                task_id=t["task_id"],
                scheduled_start=_parse_dt(t["scheduled_start"]),
                estimated_token_cost=t["estimated_tokens"],
                priority_rank=t["priority_rank"],
                window_id=t["window_id"],
            )
            for t in task_rows
        ]
        meta = json.loads(row["metadata_json"])
        now = datetime.now(timezone.utc)
        return Schedule(
            schedule_id=row["schedule_id"],
            dag_name=row["dag_name"],
            created_at=_parse_dt(row["created_at"]),
            window=window,
            ordered_tasks=ordered_tasks,
            total_estimated_tokens=meta.get("total_tokens", 0),
            total_estimated_cost_usd=0.0,
            estimated_completion=now,
            is_feasible=bool(row["is_feasible"]),
            infeasibility_reason=row["reason"],
        )

    # -- TaskRun ---------------------------------------------------------------

    def save_run(self, run: TaskRun) -> None:
        self._conn.execute(
            """
            INSERT INTO task_runs (
                run_id, task_id, window_id, model, started_at, completed_at, exit_code,
                actual_input_tokens, actual_output_tokens, actual_cost_usd,
                stdout_path, stderr_path, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                completed_at         = excluded.completed_at,
                exit_code            = excluded.exit_code,
                actual_input_tokens  = excluded.actual_input_tokens,
                actual_output_tokens = excluded.actual_output_tokens,
                actual_cost_usd      = excluded.actual_cost_usd,
                stdout_path          = excluded.stdout_path,
                stderr_path          = excluded.stderr_path,
                status               = excluded.status
            """,
            (
                run.run_id,
                run.task_id,
                run.window_id,
                run.model,
                run.started_at.isoformat(),
                run.completed_at.isoformat() if run.completed_at else None,
                run.exit_code,
                run.actual_input_tokens,
                run.actual_output_tokens,
                run.actual_cost_usd,
                run.stdout_path,
                run.stderr_path,
                run.status.value,
            ),
        )
        self._conn.commit()

    def get_runs_for_task(self, task_id: str) -> list[TaskRun]:
        rows = self._conn.execute(
            "SELECT * FROM task_runs WHERE task_id = ? ORDER BY started_at DESC",
            (task_id,),
        ).fetchall()
        return [_row_to_run(r) for r in rows]

    def get_runs_in_window(self, window_id: str) -> list[TaskRun]:
        rows = self._conn.execute(
            "SELECT * FROM task_runs WHERE window_id = ? ORDER BY started_at",
            (window_id,),
        ).fetchall()
        return [_row_to_run(r) for r in rows]

    def get_recent_runs(self, limit: int = 20) -> list[TaskRun]:
        rows = self._conn.execute(
            "SELECT * FROM task_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_run(r) for r in rows]

    # -- CostEstimate ----------------------------------------------------------

    def get_cost_estimate(self, task_type: str, model: str) -> CostEstimate | None:
        row = self._conn.execute(
            "SELECT * FROM cost_estimates WHERE task_type = ? AND model = ?",
            (task_type, model),
        ).fetchone()
        if row is None:
            return None
        return CostEstimate(
            task_type=row["task_type"],
            model=row["model"],
            p50_tokens=row["p50_tokens"],
            p90_tokens=row["p90_tokens"],
            sample_count=row["sample_count"],
            last_updated=_parse_dt(row["last_updated"]),
        )

    def upsert_cost_estimate(self, estimate: CostEstimate) -> None:
        self._conn.execute(
            """
            INSERT INTO cost_estimates (task_type, model, p50_tokens, p90_tokens, sample_count, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_type, model) DO UPDATE SET
                p50_tokens   = excluded.p50_tokens,
                p90_tokens   = excluded.p90_tokens,
                sample_count = excluded.sample_count,
                last_updated = excluded.last_updated
            """,
            (
                estimate.task_type,
                estimate.model,
                estimate.p50_tokens,
                estimate.p90_tokens,
                estimate.sample_count,
                estimate.last_updated.isoformat(),
            ),
        )
        self._conn.commit()

    # -- RateLimitEvent --------------------------------------------------------

    def save_rate_limit_event(self, event: RateLimitEvent) -> None:
        self._conn.execute(
            """
            INSERT INTO rate_limit_events
                (event_id, occurred_at, tokens_consumed, retry_after_seconds, window_start_estimate)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (
                event.event_id,
                event.occurred_at.isoformat(),
                event.tokens_consumed,
                event.retry_after_seconds,
                event.window_start_estimate.isoformat(),
            ),
        )
        self._conn.commit()

    def get_rate_limit_events(self, limit: int = 50) -> list[RateLimitEvent]:
        rows = self._conn.execute(
            "SELECT * FROM rate_limit_events ORDER BY occurred_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            RateLimitEvent(
                event_id=r["event_id"],
                occurred_at=_parse_dt(r["occurred_at"]),
                tokens_consumed=r["tokens_consumed"],
                retry_after_seconds=r["retry_after_seconds"],
                window_start_estimate=_parse_dt(r["window_start_estimate"]),
            )
            for r in rows
        ]

    def get_task_samples(self, task_type: str, model: str, limit: int = 50) -> list[int]:
        """Return the last N actual token totals for a task type + model."""
        rows = self._conn.execute(
            """
            SELECT tr.actual_input_tokens + tr.actual_output_tokens AS total
            FROM task_runs tr
            JOIN scheduled_tasks st ON tr.task_id = st.task_id
            WHERE tr.model = ? AND tr.status = 'completed'
            ORDER BY tr.started_at DESC
            LIMIT ?
            """,
            (model, limit),
        ).fetchall()
        # Fall back: if we can't join, just return all completed runs for this model
        if not rows:
            rows = self._conn.execute(
                """
                SELECT actual_input_tokens + actual_output_tokens AS total
                FROM task_runs
                WHERE model = ? AND status = 'completed'
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (model, limit),
            ).fetchall()
        return [r["total"] for r in rows]


def _row_to_run(row: sqlite3.Row) -> TaskRun:
    return TaskRun(
        run_id=row["run_id"],
        task_id=row["task_id"],
        window_id=row["window_id"],
        model=row["model"],
        started_at=_parse_dt(row["started_at"]),
        completed_at=_parse_dt(row["completed_at"]),
        exit_code=row["exit_code"],
        actual_input_tokens=row["actual_input_tokens"],
        actual_output_tokens=row["actual_output_tokens"],
        actual_cost_usd=row["actual_cost_usd"],
        stdout_path=row["stdout_path"],
        stderr_path=row["stderr_path"],
        status=RunStatus(row["status"]),
    )
