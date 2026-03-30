"""DAG construction and analysis engine using networkx."""

from __future__ import annotations

import networkx as nx

from claude_flow.graph.models import ProjectDAG, Task, TaskStatus


class DAGEngine:
    """Build and analyze a directed acyclic graph of tasks."""

    def __init__(self) -> None:
        self.graph: nx.DiGraph = nx.DiGraph()
        self._tasks: dict[str, Task] = {}
        self.dag: ProjectDAG | None = None

    # -- Construction --------------------------------------------------------

    def add_task(self, task: Task) -> None:
        self._tasks[task.id] = task
        self.graph.add_node(task.id)

    def add_dependency(self, from_id: str, to_id: str) -> None:
        """Add edge: from_id must complete before to_id can start."""
        for tid in (from_id, to_id):
            if tid not in self._tasks:
                raise ValueError(f"Unknown task: {tid}")
        self.graph.add_edge(from_id, to_id, weight=self._tasks[from_id].estimated_hours)

    def get_task(self, task_id: str) -> Task:
        return self._tasks[task_id]

    @property
    def tasks(self) -> dict[str, Task]:
        return dict(self._tasks)

    # -- Validation ----------------------------------------------------------

    def has_cycle(self) -> bool:
        return not nx.is_directed_acyclic_graph(self.graph)

    def validate(self) -> None:
        if self.has_cycle():
            cycles = list(nx.simple_cycles(self.graph))
            raise ValueError(f"Cycle detected: {cycles[0]}")

    # -- Analysis ------------------------------------------------------------

    def topological_sort(self) -> list[str]:
        self.validate()
        return list(nx.topological_sort(self.graph))

    def topological_levels(self) -> list[list[str]]:
        """Group tasks by depth level (tasks at same level can run in parallel)."""
        self.validate()
        return [sorted(gen) for gen in nx.topological_generations(self.graph)]

    def critical_path(self) -> tuple[list[str], float]:
        """Return (path, total_hours) for the longest path through the DAG."""
        self.validate()
        if not self._tasks:
            return [], 0.0

        path = nx.dag_longest_path(self.graph, weight="weight")
        if not path:
            return [], 0.0

        total = sum(self._tasks[tid].estimated_hours for tid in path)
        return path, total

    def predecessors(self, task_id: str) -> list[str]:
        return list(self.graph.predecessors(task_id))

    def successors(self, task_id: str) -> list[str]:
        return list(self.graph.successors(task_id))

    def update_blocked_statuses(self) -> None:
        """Mark tasks as BLOCKED if any predecessor is not DONE."""
        for tid in self.topological_sort():
            task = self._tasks[tid]
            if task.status == TaskStatus.DONE:
                continue
            preds = self.predecessors(tid)
            if preds and any(self._tasks[p].status != TaskStatus.DONE for p in preds):
                task.status = TaskStatus.BLOCKED

    # -- Export ---------------------------------------------------------------

    def to_project_dag(self) -> ProjectDAG:
        """Export the current engine state back to a ProjectDAG."""
        tasks = list(self._tasks.values())
        dependencies = list(self.graph.edges())
        name = self.dag.name if self.dag else "Untitled"
        return ProjectDAG(name=name, tasks=tasks, dependencies=dependencies)

    # -- Factory -------------------------------------------------------------

    @classmethod
    def from_project_dag(cls, dag: ProjectDAG) -> DAGEngine:
        engine = cls()
        engine.dag = dag
        for task in dag.tasks:
            engine.add_task(task)
        for from_id, to_id in dag.dependencies:
            engine.add_dependency(from_id, to_id)
        engine.update_blocked_statuses()
        return engine
