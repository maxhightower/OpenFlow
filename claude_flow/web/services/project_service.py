"""Multi-project coordination: manages one SchedulerEngine per loaded project."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from claude_flow.graph.engine import DAGEngine
from claude_flow.graph.models import ProjectDAG, Task, TaskType, TaskStatus
from claude_flow.scheduler.engine import SchedulerEngine
from claude_flow.scheduler.estimator import CostEstimator
from claude_flow.scheduler.optimizer import BudgetOptimizer
from claude_flow.web.store import WebStore


class ProjectService:
    """Holds a cache of SchedulerEngines keyed by project_id."""

    def __init__(self, store: WebStore, token_budget: int, model: str) -> None:
        self.store = store
        self.token_budget = token_budget
        self.model = model
        self._engines: dict[str, SchedulerEngine] = {}

    def get_engine(self, project_id: str) -> SchedulerEngine:
        if project_id in self._engines:
            return self._engines[project_id]
        project = self.store.get_project(project_id)
        if project is None:
            raise KeyError(f"Project not found: {project_id}")
        dag = ProjectDAG.from_dict(project["dag_json"])
        dag_engine = DAGEngine.from_project_dag(dag)
        estimator = CostEstimator(self.store)
        optimizer = BudgetOptimizer()
        engine = SchedulerEngine(
            dag_engine=dag_engine,
            store=self.store,
            estimator=estimator,
            optimizer=optimizer,
            token_budget_per_window=self.token_budget,
            model=self.model,
        )
        self._engines[project_id] = engine
        return engine

    def invalidate(self, project_id: str) -> None:
        self._engines.pop(project_id, None)

    def save_dag(self, project_id: str, engine: SchedulerEngine) -> None:
        dag = engine.dag_engine.to_project_dag()
        self.store.save_project_dag(project_id, json.dumps(dag.to_dict()))

    def create_project(self, name: str, description: str, dag_dict: dict) -> dict:
        project_id = str(uuid.uuid4())
        dag_json = json.dumps(dag_dict)
        return self.store.create_project(
            project_id=project_id,
            name=name,
            description=description,
            dag_json=dag_json,
        )

    def list_projects(self, include_archived: bool = False) -> list[dict]:
        projects = self.store.list_projects(include_archived=include_archived)
        for p in projects:
            dag_data = p["dag_json"]
            tasks = dag_data.get("tasks", [])
            done = sum(1 for t in tasks if t.get("status") == "done")
            total = len(tasks)
            p["task_count"] = total
            p["tasks_done"] = done
            p["progress_pct"] = round(done / total * 100, 1) if total > 0 else 0.0
        return projects
