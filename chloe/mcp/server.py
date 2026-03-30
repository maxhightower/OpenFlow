"""MCP server for Chloe — exposes scheduler tools to Claude Code."""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

from chloe.scheduler.engine import SchedulerEngine
from chloe.mcp.tools import ChloeTools, DAGPersistence

# ---------------------------------------------------------------------------
# Tool schema definitions
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[types.Tool] = [
    types.Tool(
        name="get_budget_status",
        description=(
            "Returns the current token budget window status: how many tokens have been used, "
            "how many remain, how much time is left in the 5-hour window, and estimated cost."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    types.Tool(
        name="get_schedule",
        description=(
            "Returns the optimized execution plan for the current DAG. "
            "Shows which tasks will run, in what order, with estimated token costs."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    types.Tool(
        name="get_next_task",
        description=(
            "Returns the single next task that should be executed based on priority, "
            "dependencies, and remaining budget. Returns null if no tasks are runnable."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    types.Tool(
        name="queue_task",
        description="Add a new task to the scheduler DAG.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human-readable task name"},
                "task_type": {
                    "type": "string",
                    "enum": ["feature", "bug-fix", "refactor", "test", "docs", "release"],
                    "description": "Task category",
                },
                "priority": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "Priority 1 (highest) to 10 (lowest). Default 5.",
                },
                "estimated_hours": {
                    "type": "number",
                    "description": "Estimated wall-clock hours",
                },
                "estimated_tokens": {
                    "type": "integer",
                    "description": "Estimated token cost. 0 = use default for task type.",
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of task IDs that must complete before this one.",
                },
            },
            "required": ["name"],
        },
    ),
    types.Tool(
        name="run_next_task",
        description=(
            "Execute the next scheduled task via the claude CLI. "
            "Returns the run result including actual token usage."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, show what would run without executing. Default false.",
                },
            },
            "required": [],
        },
    ),
    types.Tool(
        name="mark_task_done",
        description="Manually mark a task as completed without executing it.",
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task ID to mark complete"},
                "actual_tokens": {
                    "type": "integer",
                    "description": "Actual tokens used (optional, for reconciliation)",
                },
            },
            "required": ["task_id"],
        },
    ),
    types.Tool(
        name="get_task_status",
        description="Return the current status and run history for a specific task.",
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task ID to query"},
            },
            "required": ["task_id"],
        },
    ),
    types.Tool(
        name="list_runs",
        description="Return recent task execution history.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of runs to return. Default 20.",
                },
                "window_id": {
                    "type": "string",
                    "description": "Filter to a specific budget window ID.",
                },
            },
            "required": [],
        },
    ),
    types.Tool(
        name="optimize_schedule",
        description=(
            "Run the OR-Tools CP-SAT constraint solver to produce a makespan-optimal "
            "schedule. Respects task dependencies, parallelizes across worker slots, "
            "and skips completed tasks. Returns a timeline with worker lane assignments."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "num_workers": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Number of parallel worker slots (e.g. concurrent Claude sessions). Default 1.",
                },
                "hours_per_day": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Working hours per day, for calendar-day estimates. Optional.",
                },
            },
            "required": [],
        },
    ),
]


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

async def _handle_optimize_schedule(engine: SchedulerEngine, arguments: dict) -> dict:
    """Run the CP-SAT solver on the engine's DAG."""
    from chloe.scheduler.solver import solve

    dag = engine.dag_engine.dag
    num_workers = arguments.get("num_workers", 1)
    hours_per_day = arguments.get("hours_per_day")

    schedule = solve(
        dag,
        num_workers=num_workers,
        hours_per_day=hours_per_day,
    )
    return {"schedule": schedule.format()}


def create_server(engine: SchedulerEngine, dag_persistence: DAGPersistence | None = None) -> Server:
    server = Server("chloe")
    handler = ChloeTools(engine, dag_persistence=dag_persistence)

    TOOL_HANDLERS = {
        "get_budget_status": handler.get_budget_status,
        "get_schedule": handler.get_schedule,
        "get_next_task": handler.get_next_task,
        "queue_task": handler.queue_task,
        "run_next_task": handler.run_next_task,
        "mark_task_done": handler.mark_task_done,
        "get_task_status": handler.get_task_status,
        "list_runs": handler.list_runs,
    }

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return TOOL_DEFINITIONS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        if name == "optimize_schedule":
            result = await _handle_optimize_schedule(engine, arguments or {})
        elif name in TOOL_HANDLERS:
            result = await TOOL_HANDLERS[name](arguments or {})
        else:
            raise ValueError(f"Unknown tool: {name}")
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    return server


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run_server(
    dag_file: Path | None = None,
    db_path: Path | None = None,
    token_budget: int = 500_000,
    model: str = "claude-sonnet-4-6",
) -> None:
    """Start the MCP server on stdio transport."""
    import json as _json
    from chloe.graph.engine import DAGEngine
    from chloe.graph.models import ProjectDAG
    from chloe.graph.sample import build_sample_dag

    if dag_file and dag_file.exists():
        with open(dag_file) as fh:
            data = _json.load(fh)
        dag = ProjectDAG.from_dict(data)
    else:
        dag = build_sample_dag()

    dag_engine = DAGEngine.from_project_dag(dag)
    scheduler = SchedulerEngine.from_dag_engine(
        dag_engine,
        db_path=db_path,
        token_budget=token_budget,
        model=model,
    )

    persistence = DAGPersistence(dag_file) if dag_file else None
    server = create_server(scheduler, dag_persistence=persistence)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
