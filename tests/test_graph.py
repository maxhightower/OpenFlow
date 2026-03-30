"""Tests for the graph module."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from chloe.graph.engine import DAGEngine
from chloe.graph.models import (
    ProjectDAG,
    Task,
    TaskStatus,
    TaskType,
)
from chloe.graph.render import DAGRenderer
from chloe.graph.sample import build_sample_dag


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def diamond_engine() -> DAGEngine:
    """Diamond: A -> {B, C} -> D"""
    engine = DAGEngine()
    engine.add_task(Task("A", "Task A", TaskType.BUG_FIX, 2.0, TaskStatus.DONE))
    engine.add_task(Task("B", "Task B", TaskType.FEATURE, 5.0, TaskStatus.IN_PROGRESS))
    engine.add_task(Task("C", "Task C", TaskType.REFACTOR, 3.0, TaskStatus.PENDING))
    engine.add_task(Task("D", "Task D", TaskType.RELEASE, 1.0, TaskStatus.PENDING))
    engine.add_dependency("A", "B")
    engine.add_dependency("A", "C")
    engine.add_dependency("B", "D")
    engine.add_dependency("C", "D")
    return engine


@pytest.fixture
def sample_engine() -> DAGEngine:
    return DAGEngine.from_project_dag(build_sample_dag())


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestTaskModel:
    def test_default_values(self):
        t = Task("x", "My task")
        assert t.task_type == TaskType.FEATURE
        assert t.status == TaskStatus.PENDING
        assert t.estimated_hours == 1.0

    def test_to_from_dict_roundtrip(self):
        t = Task("x", "My task", TaskType.BUG_FIX, 3.5, TaskStatus.DONE)
        d = t.to_dict()
        t2 = Task.from_dict(d)
        assert t2.id == t.id
        assert t2.name == t.name
        assert t2.task_type == t.task_type
        assert t2.estimated_hours == t.estimated_hours
        assert t2.status == t.status

    def test_project_dag_roundtrip(self):
        dag = build_sample_dag()
        d = dag.to_dict()
        dag2 = ProjectDAG.from_dict(d)
        assert dag2.name == dag.name
        assert len(dag2.tasks) == len(dag.tasks)
        assert len(dag2.dependencies) == len(dag.dependencies)


# ---------------------------------------------------------------------------
# Engine tests
# ---------------------------------------------------------------------------


class TestDAGEngine:
    def test_add_tasks(self, diamond_engine: DAGEngine):
        assert len(diamond_engine.tasks) == 4

    def test_add_dependency_missing_node(self):
        engine = DAGEngine()
        engine.add_task(Task("A", "A"))
        with pytest.raises(ValueError, match="Unknown task"):
            engine.add_dependency("A", "Z")

    def test_no_cycle(self, diamond_engine: DAGEngine):
        assert not diamond_engine.has_cycle()

    def test_cycle_detection(self):
        engine = DAGEngine()
        engine.add_task(Task("A", "A"))
        engine.add_task(Task("B", "B"))
        engine.add_task(Task("C", "C"))
        engine.add_dependency("A", "B")
        engine.add_dependency("B", "C")
        engine.add_dependency("C", "A")
        assert engine.has_cycle()
        with pytest.raises(ValueError, match="Cycle"):
            engine.validate()

    def test_topological_sort(self, diamond_engine: DAGEngine):
        order = diamond_engine.topological_sort()
        assert order.index("A") < order.index("B")
        assert order.index("A") < order.index("C")
        assert order.index("B") < order.index("D")

    def test_topological_levels(self, diamond_engine: DAGEngine):
        levels = diamond_engine.topological_levels()
        assert levels[0] == ["A"]
        assert sorted(levels[1]) == ["B", "C"]
        assert levels[2] == ["D"]

    def test_critical_path(self, diamond_engine: DAGEngine):
        path, hours = diamond_engine.critical_path()
        # A(2) -> B(5) -> D(1) = 8h is longer than A(2) -> C(3) -> D(1) = 6h
        assert path == ["A", "B", "D"]
        assert hours == 8.0

    def test_predecessors_successors(self, diamond_engine: DAGEngine):
        assert diamond_engine.predecessors("D") == ["B", "C"] or set(
            diamond_engine.predecessors("D")
        ) == {"B", "C"}
        assert set(diamond_engine.successors("A")) == {"B", "C"}

    def test_update_blocked_statuses(self, diamond_engine: DAGEngine):
        diamond_engine.update_blocked_statuses()
        # A is DONE, B is IN_PROGRESS (predecessor done, so stays)
        assert diamond_engine.get_task("B").status == TaskStatus.IN_PROGRESS
        # C is PENDING, but predecessor A is DONE, so stays PENDING
        assert diamond_engine.get_task("C").status == TaskStatus.PENDING
        # D has predecessors B (in-progress) and C (pending), so BLOCKED
        assert diamond_engine.get_task("D").status == TaskStatus.BLOCKED

    def test_empty_engine(self):
        engine = DAGEngine()
        path, hours = engine.critical_path()
        assert path == []
        assert hours == 0.0

    def test_from_project_dag(self):
        dag = build_sample_dag()
        engine = DAGEngine.from_project_dag(dag)
        assert len(engine.tasks) == 12
        assert not engine.has_cycle()


# ---------------------------------------------------------------------------
# Sample DAG tests
# ---------------------------------------------------------------------------


class TestSampleDAG:
    def test_is_acyclic(self, sample_engine: DAGEngine):
        assert not sample_engine.has_cycle()

    def test_has_critical_path(self, sample_engine: DAGEngine):
        path, hours = sample_engine.critical_path()
        assert len(path) > 0
        assert hours > 0

    def test_has_multiple_levels(self, sample_engine: DAGEngine):
        levels = sample_engine.topological_levels()
        assert len(levels) >= 5


# ---------------------------------------------------------------------------
# Renderer tests (smoke)
# ---------------------------------------------------------------------------


class TestDAGRenderer:
    def test_render_sample(self, sample_engine: DAGEngine):
        output = StringIO()
        renderer = DAGRenderer(sample_engine, Console(file=output, force_terminal=True, width=120))
        renderer.render()
        text = output.getvalue()
        assert "Dependency Graph" in text
        assert "Critical Path" in text
        assert "Analysis" in text

    def test_render_diamond(self, diamond_engine: DAGEngine):
        output = StringIO()
        renderer = DAGRenderer(diamond_engine, Console(file=output, force_terminal=True, width=120))
        renderer.render()
        text = output.getvalue()
        assert "Task A" in text
        assert "Task D" in text

    def test_render_empty(self):
        engine = DAGEngine()
        output = StringIO()
        renderer = DAGRenderer(engine, Console(file=output, force_terminal=True, width=120))
        renderer.render()
        assert "Empty DAG" in output.getvalue()


# ---------------------------------------------------------------------------
# Serialization tests
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_json_round_trip(self):
        dag = build_sample_dag()
        data = dag.to_dict()
        json_str = json.dumps(data)
        loaded = ProjectDAG.from_dict(json.loads(json_str))
        assert loaded.name == dag.name
        assert len(loaded.tasks) == len(dag.tasks)

    def test_load_from_file(self, tmp_path: Path):
        dag = build_sample_dag()
        fpath = tmp_path / "tasks.json"
        fpath.write_text(json.dumps(dag.to_dict()))

        with open(fpath) as f:
            data = json.load(f)
        loaded = ProjectDAG.from_dict(data)
        assert loaded.name == "Project Phoenix v2.0"
        assert len(loaded.tasks) == 12
