"""Tests for the ClaudeFlow web API layer."""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from fastapi.testclient import TestClient

from claude_flow.web.app import create_app
from claude_flow.web.config import WebConfig


@pytest.fixture()
def client(tmp_path: Path):
    config = WebConfig(
        db_path=tmp_path / "test.db",
        token_budget=500_000,
        model="claude-sonnet-4-6",
    )
    app = create_app(config)
    with TestClient(app) as c:
        yield c


# -- Health --------------------------------------------------------------------

def test_health(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


# -- Projects CRUD ------------------------------------------------------------

def test_create_and_list_projects(client: TestClient) -> None:
    resp = client.post("/api/projects", json={
        "name": "Test Project",
        "description": "A test",
        "dag": {
            "name": "Test Project",
            "tasks": [
                {"id": "T1", "name": "Task one", "task_type": "feature", "status": "pending"},
            ],
            "dependencies": [],
        },
    })
    assert resp.status_code == 201
    project = resp.json()
    assert project["name"] == "Test Project"
    project_id = project["project_id"]

    # List
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    projects = resp.json()
    assert len(projects) == 1
    assert projects[0]["project_id"] == project_id
    assert projects[0]["task_count"] == 1

    # Get
    resp = client.get(f"/api/projects/{project_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test Project"


def test_update_project(client: TestClient) -> None:
    resp = client.post("/api/projects", json={"name": "Original"})
    project_id = resp.json()["project_id"]

    resp = client.put(f"/api/projects/{project_id}", json={"name": "Updated"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated"


def test_delete_project(client: TestClient) -> None:
    resp = client.post("/api/projects", json={"name": "To Delete"})
    project_id = resp.json()["project_id"]

    resp = client.delete(f"/api/projects/{project_id}")
    assert resp.status_code == 204

    # Should not appear in default list
    resp = client.get("/api/projects")
    assert len(resp.json()) == 0


def test_get_nonexistent_project(client: TestClient) -> None:
    resp = client.get("/api/projects/nonexistent")
    assert resp.status_code == 404


# -- DAG endpoints -------------------------------------------------------------

def _create_project_with_dag(client: TestClient) -> str:
    resp = client.post("/api/projects", json={
        "name": "DAG Test",
        "dag": {
            "name": "DAG Test",
            "tasks": [
                {"id": "T1", "name": "First", "task_type": "feature", "status": "done", "priority": 1},
                {"id": "T2", "name": "Second", "task_type": "test", "status": "pending", "priority": 2},
            ],
            "dependencies": [["T1", "T2"]],
        },
    })
    return resp.json()["project_id"]


def test_get_dag(client: TestClient) -> None:
    pid = _create_project_with_dag(client)
    resp = client.get(f"/api/projects/{pid}/dag")
    assert resp.status_code == 200
    dag = resp.json()
    assert dag["name"] == "DAG Test"
    assert len(dag["tasks"]) == 2


def test_get_analysis(client: TestClient) -> None:
    pid = _create_project_with_dag(client)
    resp = client.get(f"/api/projects/{pid}/dag/analysis")
    assert resp.status_code == 200
    analysis = resp.json()
    assert analysis["total_tasks"] == 2
    assert analysis["done"] == 1
    assert analysis["edge_count"] == 1
    assert len(analysis["critical_path"]) > 0


def test_add_task(client: TestClient) -> None:
    pid = _create_project_with_dag(client)
    resp = client.post(f"/api/projects/{pid}/dag/tasks", json={
        "name": "Third task",
        "task_type": "docs",
        "priority": 3,
        "depends_on": ["T2"],
    })
    assert resp.status_code == 201
    task = resp.json()
    assert task["name"] == "Third task"
    assert task["task_type"] == "docs"


def test_update_task(client: TestClient) -> None:
    pid = _create_project_with_dag(client)
    resp = client.put(f"/api/projects/{pid}/dag/tasks/T2", json={
        "name": "Updated name",
        "priority": 1,
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated name"
    assert resp.json()["priority"] == 1


def test_delete_task(client: TestClient) -> None:
    pid = _create_project_with_dag(client)
    resp = client.delete(f"/api/projects/{pid}/dag/tasks/T2")
    assert resp.status_code == 204

    resp = client.get(f"/api/projects/{pid}/dag")
    assert len(resp.json()["tasks"]) == 1


def test_add_and_delete_edge(client: TestClient) -> None:
    resp = client.post("/api/projects", json={
        "name": "Edge Test",
        "dag": {
            "name": "Edge Test",
            "tasks": [
                {"id": "A", "name": "A", "task_type": "feature", "status": "pending"},
                {"id": "B", "name": "B", "task_type": "feature", "status": "pending"},
                {"id": "C", "name": "C", "task_type": "feature", "status": "pending"},
            ],
            "dependencies": [],
        },
    })
    pid = resp.json()["project_id"]

    # Add edge
    resp = client.post(f"/api/projects/{pid}/dag/edges", json={
        "from_id": "A", "to_id": "B",
    })
    assert resp.status_code == 201

    # Delete edge
    resp = client.request("DELETE", f"/api/projects/{pid}/dag/edges", json={
        "from_id": "A", "to_id": "B",
    })
    assert resp.status_code == 204


def test_cycle_detection(client: TestClient) -> None:
    resp = client.post("/api/projects", json={
        "name": "Cycle Test",
        "dag": {
            "name": "Cycle Test",
            "tasks": [
                {"id": "A", "name": "A", "task_type": "feature", "status": "pending"},
                {"id": "B", "name": "B", "task_type": "feature", "status": "pending"},
            ],
            "dependencies": [["A", "B"]],
        },
    })
    pid = resp.json()["project_id"]

    # This should fail: B -> A would create A -> B -> A cycle
    resp = client.post(f"/api/projects/{pid}/dag/edges", json={
        "from_id": "B", "to_id": "A",
    })
    assert resp.status_code == 400
    assert "cycle" in resp.json()["detail"].lower()


# -- Budget --------------------------------------------------------------------

def test_budget_status(client: TestClient) -> None:
    resp = client.get("/api/budget")
    assert resp.status_code == 200
    data = resp.json()
    assert "token_budget" in data
    assert data["token_budget"] == 500_000


def test_budget_history(client: TestClient) -> None:
    # Access budget to create a window
    client.get("/api/budget")
    resp = client.get("/api/budget/history")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# -- Scheduler -----------------------------------------------------------------

def test_get_schedule(client: TestClient) -> None:
    pid = _create_project_with_dag(client)
    resp = client.get(f"/api/projects/{pid}/schedule")
    assert resp.status_code == 200
    data = resp.json()
    assert "tasks" in data
    assert "is_feasible" in data


def test_get_next_task(client: TestClient) -> None:
    pid = _create_project_with_dag(client)
    resp = client.get(f"/api/projects/{pid}/next-task")
    assert resp.status_code == 200
    data = resp.json()
    # T2 should be next (T1 is done)
    if data.get("task"):
        assert data["task"]["id"] == "T2"


def test_mark_task_done(client: TestClient) -> None:
    pid = _create_project_with_dag(client)
    resp = client.post(f"/api/projects/{pid}/tasks/T2/done", json={
        "actual_tokens": 5000,
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# -- Runs ----------------------------------------------------------------------

def test_list_runs_empty(client: TestClient) -> None:
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


# -- Analytics -----------------------------------------------------------------

def test_analytics_burn_rate(client: TestClient) -> None:
    resp = client.get("/api/analytics/burn-rate")
    assert resp.status_code == 200
    assert "tokens_per_hour" in resp.json()


def test_analytics_hourly(client: TestClient) -> None:
    resp = client.get("/api/analytics/usage/hourly")
    assert resp.status_code == 200
    assert len(resp.json()) == 24


# -- Cross-project deps -------------------------------------------------------

def test_cross_deps_crud(client: TestClient) -> None:
    # Create two projects
    r1 = client.post("/api/projects", json={"name": "P1"})
    r2 = client.post("/api/projects", json={"name": "P2"})
    p1 = r1.json()["project_id"]
    p2 = r2.json()["project_id"]

    # Add cross dep
    resp = client.post("/api/cross-deps", json={
        "from_project_id": p1,
        "from_task_id": "T1",
        "to_project_id": p2,
        "to_task_id": "T1",
    })
    assert resp.status_code == 201
    dep_id = resp.json()["id"]

    # List
    resp = client.get("/api/cross-deps")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # Delete
    resp = client.delete(f"/api/cross-deps/{dep_id}")
    assert resp.status_code == 204

    resp = client.get("/api/cross-deps")
    assert len(resp.json()) == 0
