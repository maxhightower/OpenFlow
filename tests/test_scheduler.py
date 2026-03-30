"""Tests for the scheduler module."""

from __future__ import annotations

import pytest

from claude_flow.graph.models import (
    ProjectDAG,
    Task,
    TaskStatus,
    TaskType,
)
from claude_flow.graph.sample import build_sample_dag
from claude_flow.scheduler.solver import Schedule, ScheduledTask, solve


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _linear_dag() -> ProjectDAG:
    """A -> B -> C, each 2h."""
    return ProjectDAG(
        name="linear",
        tasks=[
            Task(id="a", name="A", estimated_hours=2.0),
            Task(id="b", name="B", estimated_hours=2.0),
            Task(id="c", name="C", estimated_hours=2.0),
        ],
        dependencies=[("a", "b"), ("b", "c")],
    )


def _diamond_dag() -> ProjectDAG:
    """A -> {B, C} -> D."""
    return ProjectDAG(
        name="diamond",
        tasks=[
            Task(id="a", name="A", estimated_hours=1.0),
            Task(id="b", name="B", estimated_hours=3.0),
            Task(id="c", name="C", estimated_hours=2.0),
            Task(id="d", name="D", estimated_hours=1.0),
        ],
        dependencies=[("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")],
    )


def _parallel_dag() -> ProjectDAG:
    """Three independent tasks, no dependencies."""
    return ProjectDAG(
        name="parallel",
        tasks=[
            Task(id="x", name="X", estimated_hours=3.0),
            Task(id="y", name="Y", estimated_hours=2.0),
            Task(id="z", name="Z", estimated_hours=4.0),
        ],
        dependencies=[],
    )


# ---------------------------------------------------------------------------
# Basic solve tests
# ---------------------------------------------------------------------------


class TestLinearDAG:
    def test_single_worker(self):
        s = solve(_linear_dag(), num_workers=1)
        assert s.makespan == 6
        assert len(s.tasks) == 3

    def test_multiple_workers_doesnt_help(self):
        """Linear chain can't be parallelized."""
        s = solve(_linear_dag(), num_workers=3)
        assert s.makespan == 6

    def test_dependency_order(self):
        s = solve(_linear_dag(), num_workers=1)
        by_id = {st.task.id: st for st in s.tasks}
        assert by_id["a"].end <= by_id["b"].start
        assert by_id["b"].end <= by_id["c"].start


class TestDiamondDAG:
    def test_single_worker(self):
        s = solve(_diamond_dag(), num_workers=1)
        # Must be sequential: 1 + 3 + 2 + 1 = 7 or 1 + 2 + 3 + 1 = 7
        assert s.makespan == 7

    def test_two_workers(self):
        s = solve(_diamond_dag(), num_workers=2)
        # A(1h), then B(3h) and C(2h) in parallel, then D(1h) = 1+3+1 = 5
        assert s.makespan == 5

    def test_dependencies_respected(self):
        s = solve(_diamond_dag(), num_workers=2)
        by_id = {st.task.id: st for st in s.tasks}
        assert by_id["a"].end <= by_id["b"].start
        assert by_id["a"].end <= by_id["c"].start
        assert by_id["b"].end <= by_id["d"].start
        assert by_id["c"].end <= by_id["d"].start


class TestParallelDAG:
    def test_single_worker(self):
        s = solve(_parallel_dag(), num_workers=1)
        assert s.makespan == 9  # 3 + 2 + 4

    def test_two_workers(self):
        s = solve(_parallel_dag(), num_workers=2)
        # Best: Z(4h) on one, X(3h)+Y(2h)=5h on other -> 5h
        # Or: X(3h)+Z(4h) can't fit better. Optimal is 5.
        assert s.makespan == 5

    def test_three_workers(self):
        s = solve(_parallel_dag(), num_workers=3)
        # All parallel, longest is 4h
        assert s.makespan == 4


# ---------------------------------------------------------------------------
# Skip done tasks
# ---------------------------------------------------------------------------


class TestSkipDone:
    def test_done_tasks_excluded(self):
        dag = ProjectDAG(
            name="partial",
            tasks=[
                Task(id="a", name="A", estimated_hours=2.0, status=TaskStatus.DONE),
                Task(id="b", name="B", estimated_hours=3.0, status=TaskStatus.PENDING),
            ],
            dependencies=[("a", "b")],
        )
        s = solve(dag, skip_done=True)
        assert len(s.tasks) == 1
        assert s.tasks[0].task.id == "b"
        assert s.makespan == 3

    def test_all_done(self):
        dag = ProjectDAG(
            name="alldone",
            tasks=[
                Task(id="a", name="A", estimated_hours=2.0, status=TaskStatus.DONE),
            ],
            dependencies=[],
        )
        s = solve(dag, skip_done=True)
        assert len(s.tasks) == 0
        assert s.makespan == 0

    def test_skip_done_false(self):
        dag = ProjectDAG(
            name="include",
            tasks=[
                Task(id="a", name="A", estimated_hours=2.0, status=TaskStatus.DONE),
                Task(id="b", name="B", estimated_hours=3.0),
            ],
            dependencies=[("a", "b")],
        )
        s = solve(dag, skip_done=False)
        assert len(s.tasks) == 2
        assert s.makespan == 5


# ---------------------------------------------------------------------------
# Schedule data model
# ---------------------------------------------------------------------------


class TestScheduleModel:
    def test_efficiency(self):
        s = solve(_parallel_dag(), num_workers=3)
        # 9h work / (4h * 3 workers) = 0.75
        assert s.efficiency == pytest.approx(9 / 12, abs=0.01)

    def test_calendar_days(self):
        s = solve(_linear_dag(), num_workers=1, hours_per_day=8)
        assert s.calendar_days == pytest.approx(6 / 8)

    def test_calendar_days_none_without_hours(self):
        s = solve(_linear_dag(), num_workers=1)
        assert s.calendar_days is None

    def test_format_output(self):
        s = solve(_diamond_dag(), num_workers=2, hours_per_day=8)
        text = s.format()
        assert "=== Schedule ===" in text
        assert "Makespan:" in text
        assert "Worker" in text
        assert "Timeline" in text


# ---------------------------------------------------------------------------
# Sample DAG integration
# ---------------------------------------------------------------------------


class TestSampleDAG:
    def test_sample_solves(self):
        dag = build_sample_dag()
        s = solve(dag, num_workers=2, hours_per_day=8)
        assert s.makespan > 0
        assert len(s.tasks) > 0

    def test_more_workers_not_worse(self):
        dag = build_sample_dag()
        s1 = solve(dag, num_workers=1)
        s2 = solve(dag, num_workers=2)
        s3 = solve(dag, num_workers=4)
        assert s2.makespan <= s1.makespan
        assert s3.makespan <= s2.makespan


# ---------------------------------------------------------------------------
# Worker assignment
# ---------------------------------------------------------------------------


class TestWorkerAssignment:
    def test_workers_within_bounds(self):
        s = solve(_parallel_dag(), num_workers=3)
        for st in s.tasks:
            assert 0 <= st.worker < 3

    def test_no_overlap_on_same_worker(self):
        s = solve(_diamond_dag(), num_workers=2)
        for w in range(2):
            worker_tasks = sorted(
                [st for st in s.tasks if st.worker == w],
                key=lambda st: st.start,
            )
            for i in range(len(worker_tasks) - 1):
                assert worker_tasks[i].end <= worker_tasks[i + 1].start


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_single_task(self):
        dag = ProjectDAG(
            name="one",
            tasks=[Task(id="only", name="Only", estimated_hours=5.0)],
            dependencies=[],
        )
        s = solve(dag, num_workers=1)
        assert s.makespan == 5
        assert len(s.tasks) == 1

    def test_empty_dag(self):
        dag = ProjectDAG(name="empty", tasks=[], dependencies=[])
        s = solve(dag, num_workers=1)
        assert s.makespan == 0
        assert len(s.tasks) == 0
