"""Chloe - Resource optimization scheduler for Claude Code subscriptions."""

__version__ = "0.1.0"

# Observer module — usage log parsing, storage, and reporting
from chloe.observer.parser import UsageParser, SessionRecord, TokenEvent, estimate_cost
from chloe.observer.store import UsageStore
from chloe.observer.report import UsageReport

# Graph module — task DAG construction, analysis, and visualization
from chloe.graph.models import Task, TaskType, TaskStatus, ProjectDAG
from chloe.graph.engine import DAGEngine
from chloe.graph.render import DAGRenderer
from chloe.graph.sample import build_sample_dag

# Scheduler module — OR-Tools CP-SAT constraint optimizer
from chloe.scheduler.solver import solve as solve_optimal

__all__ = [
    # Observer
    "UsageParser",
    "SessionRecord",
    "TokenEvent",
    "UsageStore",
    "UsageReport",
    "estimate_cost",
    # Graph
    "Task",
    "TaskType",
    "TaskStatus",
    "ProjectDAG",
    "DAGEngine",
    "DAGRenderer",
    "build_sample_dag",
    # Scheduler
    "solve_optimal",
]
