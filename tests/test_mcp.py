"""Tests for the MCP server module."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chloe.graph.engine import DAGEngine
from chloe.graph.models import Task, TaskType, TaskStatus
from chloe.graph.sample import build_sample_dag
from chloe.mcp.server import create_server, TOOL_DEFINITIONS
from chloe.mcp.tools import ChloeTools
from chloe.scheduler.engine import SchedulerEngine
from chloe.scheduler.store import SchedulerStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_store(tmp_path) -> SchedulerStore:
    return SchedulerStore(tmp_path / "test.db")


@pytest.fixture
def sample_engine(tmp_store) -> SchedulerEngine:
    """Engine loaded with the sample DAG (has 4 DONE + 8 pending tasks)."""
    dag = build_sample_dag()
    dag_engine = DAGEngine.from_project_dag(dag)
    return SchedulerEngine(
        dag_engine=dag_engine,
        store=tmp_store,
        estimator=__import__("chloe.scheduler.estimator", fromlist=["CostEstimator"]).CostEstimator(tmp_store),
        optimizer=__import__("chloe.scheduler.optimizer", fromlist=["BudgetOptimizer"]).BudgetOptimizer(),
        token_budget_per_window=500_000,
        model="claude-sonnet-4-6",
    )


@pytest.fixture
def small_engine(tmp_path) -> SchedulerEngine:
    """Engine with a small 3-task DAG: A(done) -> B(pending) -> C(pending)."""
    store = SchedulerStore(tmp_path / "small.db")
    engine = DAGEngine()
    engine.add_task(Task("A", "Setup", TaskType.FEATURE, 1.0, TaskStatus.DONE))
    engine.add_task(Task("B", "Build API", TaskType.FEATURE, 2.0, TaskStatus.PENDING, priority=1))
    engine.add_task(Task("C", "Write tests", TaskType.TEST, 1.0, TaskStatus.PENDING, priority=3))
    engine.add_dependency("A", "B")
    engine.add_dependency("B", "C")
    from chloe.scheduler.estimator import CostEstimator
    from chloe.scheduler.optimizer import BudgetOptimizer
    return SchedulerEngine(
        dag_engine=engine,
        store=store,
        estimator=CostEstimator(store),
        optimizer=BudgetOptimizer(),
        token_budget_per_window=500_000,
        model="claude-sonnet-4-6",
    )


@pytest.fixture
def tools(sample_engine) -> ChloeTools:
    return ChloeTools(sample_engine)


@pytest.fixture
def small_tools(small_engine) -> ChloeTools:
    return ChloeTools(small_engine)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


class TestToolDefinitions:
    def test_all_tools_have_names(self):
        names = {t.name for t in TOOL_DEFINITIONS}
        assert "get_budget_status" in names
        assert "get_schedule" in names
        assert "get_next_task" in names
        assert "queue_task" in names
        assert "run_next_task" in names
        assert "mark_task_done" in names
        assert "get_task_status" in names
        assert "list_runs" in names
        assert "optimize_schedule" in names

    def test_tool_count(self):
        assert len(TOOL_DEFINITIONS) == 9

    def test_all_tools_have_input_schema(self):
        for tool in TOOL_DEFINITIONS:
            assert tool.inputSchema is not None
            assert "type" in tool.inputSchema

    def test_queue_task_requires_name(self):
        qt = next(t for t in TOOL_DEFINITIONS if t.name == "queue_task")
        assert "name" in qt.inputSchema["required"]


# ---------------------------------------------------------------------------
# get_budget_status
# ---------------------------------------------------------------------------


class TestGetBudgetStatus:
    @pytest.mark.asyncio
    async def test_returns_budget_fields(self, tools):
        result = await tools.get_budget_status({})
        assert "tokens_used" in result
        assert "tokens_remaining" in result
        assert "token_budget" in result
        assert "pct_used" in result
        assert "is_active" in result

    @pytest.mark.asyncio
    async def test_fresh_window_has_full_budget(self, tools):
        result = await tools.get_budget_status({})
        assert result["tokens_remaining"] == 500_000
        assert result["tokens_used"] == 0
        assert result["is_active"] is True

    @pytest.mark.asyncio
    async def test_estimated_tasks_remaining(self, tools):
        result = await tools.get_budget_status({})
        assert "estimated_tasks_remaining" in result


# ---------------------------------------------------------------------------
# get_schedule
# ---------------------------------------------------------------------------


class TestGetSchedule:
    @pytest.mark.asyncio
    async def test_returns_schedule_structure(self, tools):
        result = await tools.get_schedule({})
        assert "tasks" in result
        assert "total_estimated_tokens" in result
        assert "is_feasible" in result
        assert "dag_name" in result

    @pytest.mark.asyncio
    async def test_small_dag_schedules_b_first(self, small_tools):
        result = await small_tools.get_schedule({})
        tasks = result["tasks"]
        assert len(tasks) > 0
        # B is the only runnable task (A is done, C depends on B)
        assert tasks[0]["id"] == "B"

    @pytest.mark.asyncio
    async def test_done_tasks_excluded(self, small_tools):
        result = await small_tools.get_schedule({})
        task_ids = {t["id"] for t in result["tasks"]}
        assert "A" not in task_ids  # A is done


# ---------------------------------------------------------------------------
# get_next_task
# ---------------------------------------------------------------------------


class TestGetNextTask:
    @pytest.mark.asyncio
    async def test_returns_task(self, small_tools):
        result = await small_tools.get_next_task({})
        assert result["task"] is not None
        assert result["task"]["id"] == "B"
        assert result["task"]["name"] == "Build API"

    @pytest.mark.asyncio
    async def test_all_done_returns_none(self, tmp_path):
        store = SchedulerStore(tmp_path / "done.db")
        engine = DAGEngine()
        engine.add_task(Task("X", "Done task", TaskType.FEATURE, 1.0, TaskStatus.DONE))
        from chloe.scheduler.estimator import CostEstimator
        from chloe.scheduler.optimizer import BudgetOptimizer
        sched = SchedulerEngine(engine, store, CostEstimator(store), BudgetOptimizer())
        tools = ChloeTools(sched)
        result = await tools.get_next_task({})
        assert result["task"] is None
        assert "reason" in result


# ---------------------------------------------------------------------------
# queue_task
# ---------------------------------------------------------------------------


class TestQueueTask:
    @pytest.mark.asyncio
    async def test_add_task(self, small_tools, small_engine):
        result = await small_tools.queue_task({"name": "Deploy to prod", "task_type": "release"})
        assert "task_id" in result
        assert result["name"] == "Deploy to prod"
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_add_task_with_dependency(self, small_tools):
        result = await small_tools.queue_task({
            "name": "Integration tests",
            "task_type": "test",
            "depends_on": ["B"],
        })
        assert "error" not in result
        assert result["status"] == "blocked"  # blocked because B isn't done

    @pytest.mark.asyncio
    async def test_add_task_invalid_dependency(self, small_tools):
        result = await small_tools.queue_task({
            "name": "Orphan task",
            "depends_on": ["NONEXISTENT"],
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_add_task_defaults(self, small_tools):
        result = await small_tools.queue_task({"name": "Quick fix"})
        assert result["status"] == "pending"


# ---------------------------------------------------------------------------
# mark_task_done
# ---------------------------------------------------------------------------


class TestMarkTaskDone:
    @pytest.mark.asyncio
    async def test_mark_existing_task(self, small_tools, small_engine):
        # B is pending and runnable
        result = await small_tools.mark_task_done({"task_id": "B"})
        assert result["ok"] is True
        assert result["task_id"] == "B"
        # Verify task is now done
        task = small_engine.dag_engine.get_task("B")
        assert task.status == TaskStatus.DONE

    @pytest.mark.asyncio
    async def test_mark_unknown_task(self, small_tools):
        result = await small_tools.mark_task_done({"task_id": "ZZZZZ"})
        assert result["ok"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_mark_with_actual_tokens(self, small_tools):
        result = await small_tools.mark_task_done({"task_id": "B", "actual_tokens": 15000})
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_mark_unblocks_successor(self, small_tools, small_engine):
        # C depends on B. Mark B done, then C should become runnable.
        await small_tools.mark_task_done({"task_id": "B"})
        result = await small_tools.get_next_task({})
        assert result["task"] is not None
        assert result["task"]["id"] == "C"


# ---------------------------------------------------------------------------
# get_task_status
# ---------------------------------------------------------------------------


class TestGetTaskStatus:
    @pytest.mark.asyncio
    async def test_known_task(self, small_tools):
        result = await small_tools.get_task_status({"task_id": "B"})
        assert result["task_id"] == "B"
        assert result["name"] == "Build API"
        assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_unknown_task(self, small_tools):
        result = await small_tools.get_task_status({"task_id": "NOPE"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_done_task_has_status(self, small_tools):
        result = await small_tools.get_task_status({"task_id": "A"})
        assert result["status"] == "done"


# ---------------------------------------------------------------------------
# list_runs
# ---------------------------------------------------------------------------


class TestListRuns:
    @pytest.mark.asyncio
    async def test_empty_runs(self, small_tools):
        result = await small_tools.list_runs({})
        assert result["runs"] == []
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_runs_after_mark_done(self, small_tools):
        await small_tools.mark_task_done({"task_id": "B"})
        result = await small_tools.list_runs({})
        assert result["count"] == 1
        assert result["runs"][0]["task_id"] == "B"
        assert result["runs"][0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_limit_param(self, small_tools):
        await small_tools.mark_task_done({"task_id": "B"})
        result = await small_tools.list_runs({"limit": 0})
        assert result["count"] == 0


# ---------------------------------------------------------------------------
# run_next_task (dry_run only — avoids needing real claude CLI)
# ---------------------------------------------------------------------------


class TestRunNextTask:
    @pytest.mark.asyncio
    async def test_dry_run(self, small_tools):
        result = await small_tools.run_next_task({"dry_run": True})
        assert result["status"] == "dry_run"
        assert result["task_id"] == "B"
        assert "command" in result

    @pytest.mark.asyncio
    async def test_no_runnable_tasks(self, tmp_path):
        store = SchedulerStore(tmp_path / "empty.db")
        engine = DAGEngine()
        engine.add_task(Task("X", "Done", TaskType.FEATURE, 1.0, TaskStatus.DONE))
        from chloe.scheduler.estimator import CostEstimator
        from chloe.scheduler.optimizer import BudgetOptimizer
        sched = SchedulerEngine(engine, store, CostEstimator(store), BudgetOptimizer())
        tools = ChloeTools(sched)
        result = await tools.run_next_task({})
        assert result["status"] == "skipped"
        assert "reason" in result


# ---------------------------------------------------------------------------
# Server creation
# ---------------------------------------------------------------------------


class TestServerCreation:
    def test_create_server_returns_server(self, sample_engine):
        server = create_server(sample_engine)
        assert server is not None
        assert server.name == "chloe"
