"""ClaudeFlow - Resource optimization scheduler for Claude Code subscriptions."""

__version__ = "0.1.0"

# Observer module — usage log parsing, storage, and reporting
from claude_flow.observer.parser import UsageParser, SessionRecord, TokenEvent, estimate_cost
from claude_flow.observer.store import UsageStore
from claude_flow.observer.report import UsageReport

# Graph module — task DAG construction, analysis, and visualization
from claude_flow.graph.models import Task, TaskType, TaskStatus, ProjectDAG
from claude_flow.graph.engine import DAGEngine
from claude_flow.graph.render import DAGRenderer
from claude_flow.graph.sample import build_sample_dag

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
]
