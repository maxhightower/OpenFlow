"""MCP server exposing ClaudeFlow tools to Claude Code."""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from claude_flow.observer.parser import UsageParser
from claude_flow.observer.store import UsageStore
from claude_flow.graph.engine import DAGEngine
from claude_flow.graph.models import ProjectDAG, TaskStatus
from claude_flow.graph.sample import build_sample_dag
from claude_flow.scheduler.solver import solve as solve_schedule

mcp = FastMCP("claudeflow")


# -- Helpers -----------------------------------------------------------------

def _load_usage(claude_dir: str | None = None) -> UsageStore:
    """Parse logs and load into an in-memory SQLite store."""
    parser = UsageParser(claude_dir=Path(claude_dir) if claude_dir else None)
    records = parser.parse_all()
    if not records:
        raise ValueError(
            "No Claude Code usage logs found. "
            "Expected JSONL files in ~/.claude/projects/"
        )
    store = UsageStore(db_path=Path(":memory:"))
    store.ingest(records)
    return store


def _load_dag(file: str | None = None) -> tuple[DAGEngine, ProjectDAG]:
    """Load a ProjectDAG from JSON file, or use the sample DAG."""
    if file:
        path = Path(file)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file}")
        with open(path) as fh:
            data = json.load(fh)
        dag = ProjectDAG.from_dict(data)
    else:
        dag = build_sample_dag()
    engine = DAGEngine.from_project_dag(dag)
    return engine, dag


# -- Tools -------------------------------------------------------------------

@mcp.tool()
def observe(claude_dir: str | None = None) -> str:
    """Scan Claude Code usage logs and return a usage summary.

    Parses all JSONL files in ~/.claude/projects/ and reports sessions,
    token counts, cost, burn rate, peak hours, and top projects.

    Args:
        claude_dir: Path to the .claude directory. Defaults to ~/.claude.
    """
    store = _load_usage(claude_dir)

    sessions = store.session_count()
    input_tok, output_tok = store.total_tokens()
    total_tok = input_tok + output_tok
    total_cost = store.total_cost()
    tokens_per_hr, cost_per_hr = store.burn_rate()
    peak_hours = store.peak_usage_hours()
    models = store.avg_tokens_by_model()
    top = store.top_projects(3)

    lines = [
        "=== ClaudeFlow Usage Report ===",
        "",
        f"Sessions: {sessions}",
        f"Total tokens: {total_tok:,} (input: {input_tok:,}, output: {output_tok:,})",
        f"Total cost: ${total_cost:,.4f}",
        "",
        "--- Burn Rate ---",
        f"Tokens/hr: {tokens_per_hr:,.0f}",
        f"Cost/hr: ${cost_per_hr:,.4f}",
        f"Projected daily: ${cost_per_hr * 24:,.2f}",
    ]

    if peak_hours:
        lines += ["", "--- Peak Usage Hours ---"]
        for hour, count in peak_hours:
            bar = "#" * min(count, 40)
            lines.append(f"  {hour:02d}:00  {bar} ({count})")

    if models:
        lines += ["", "--- Avg Tokens by Model ---"]
        for model, avg_in, avg_out in models:
            lines.append(f"  {model}: input={avg_in:,.0f} output={avg_out:,.0f}")

    if top:
        lines += ["", "--- Top Projects (by tokens) ---"]
        for project, tokens in top:
            lines.append(f"  {project}: {tokens:,}")

    store.close()
    return "\n".join(lines)


@mcp.tool()
def burn_rate(claude_dir: str | None = None) -> str:
    """Get the current token burn rate and cost rate.

    Returns tokens/hr, cost/hr, and projected daily cost based on
    all usage logs found in the Claude directory.

    Args:
        claude_dir: Path to the .claude directory. Defaults to ~/.claude.
    """
    store = _load_usage(claude_dir)

    tokens_per_hr, cost_per_hr = store.burn_rate()
    input_tok, output_tok = store.total_tokens()
    total_cost = store.total_cost()

    result = (
        f"Burn rate: {tokens_per_hr:,.0f} tokens/hr, ${cost_per_hr:,.4f}/hr\n"
        f"Projected daily cost: ${cost_per_hr * 24:,.2f}\n"
        f"Total so far: {input_tok + output_tok:,} tokens, ${total_cost:,.4f}"
    )
    store.close()
    return result


@mcp.tool()
def graph(file: str | None = None) -> str:
    """Build a task DAG and return its structure as text.

    Loads tasks and dependencies from a JSON file (or uses a built-in
    sample project) and returns the topological levels, showing which
    tasks can run in parallel.

    Args:
        file: Path to a tasks.json file. If omitted, uses a sample DAG.
    """
    engine, dag = _load_dag(file)
    levels = engine.topological_levels()
    cp_ids, cp_hours = engine.critical_path()
    cp_set = set(cp_ids)

    lines = [
        f"=== DAG: {dag.name} ===",
        f"Tasks: {len(dag.tasks)}  |  Dependencies: {len(dag.dependencies)}",
        "",
    ]

    for i, level in enumerate(levels):
        lines.append(f"Level {i} (parallel group):")
        for tid in level:
            task = engine.get_task(tid)
            marker = " [CRITICAL]" if tid in cp_set else ""
            lines.append(
                f"  [{task.task_type.value}] {task.name} "
                f"({task.estimated_hours}h, {task.status.value}){marker}"
            )
        lines.append("")

    lines.append(f"Critical path ({cp_hours}h total):")
    for tid in cp_ids:
        task = engine.get_task(tid)
        lines.append(f"  -> {task.name} ({task.estimated_hours}h)")

    max_parallel = max(len(level) for level in levels) if levels else 0
    lines.append(f"\nMax parallelism: {max_parallel} concurrent tasks")

    return "\n".join(lines)


@mcp.tool()
def critical_path(file: str | None = None) -> str:
    """Analyze the critical path and bottlenecks in a task DAG.

    Returns the longest path through the dependency graph, identifying
    which tasks are sequential bottlenecks and which are blocked.

    Args:
        file: Path to a tasks.json file. If omitted, uses a sample DAG.
    """
    engine, dag = _load_dag(file)
    cp_ids, cp_hours = engine.critical_path()

    total_hours = sum(t.estimated_hours for t in dag.tasks)
    blocked = [t for t in dag.tasks if t.status == TaskStatus.BLOCKED]
    done = [t for t in dag.tasks if t.status == TaskStatus.DONE]

    lines = [
        f"=== Critical Path Analysis: {dag.name} ===",
        "",
        f"Critical path length: {cp_hours}h ({len(cp_ids)} tasks)",
        f"Total work: {total_hours}h across {len(dag.tasks)} tasks",
        f"Parallelism ratio: {total_hours / cp_hours:.1f}x" if cp_hours else "",
        "",
        "Sequence:",
    ]

    # Find the biggest bottleneck (most successors on critical path)
    max_successors = 0
    bottleneck = None

    for tid in cp_ids:
        task = engine.get_task(tid)
        preds = engine.predecessors(tid)
        succs = engine.successors(tid)
        pred_names = ", ".join(engine.get_task(p).name for p in preds) or "none"
        succ_names = ", ".join(engine.get_task(s).name for s in succs) or "none"
        lines.append(
            f"  -> {task.name} ({task.estimated_hours}h, {task.status.value})\n"
            f"     depends on: {pred_names}\n"
            f"     blocks: {succ_names}"
        )
        if len(succs) > max_successors:
            max_successors = len(succs)
            bottleneck = task

    lines += [
        "",
        "--- Status ---",
        f"Done: {len(done)}/{len(dag.tasks)}",
        f"Blocked: {len(blocked)}/{len(dag.tasks)}",
    ]

    if bottleneck:
        lines.append(
            f"Biggest bottleneck: {bottleneck.name} "
            f"(blocks {max_successors} task(s))"
        )

    return "\n".join(lines)


@mcp.tool()
def schedule(
    file: str | None = None,
    num_workers: int = 1,
    hours_per_day: int | None = None,
) -> str:
    """Optimize a task schedule using constraint solving.

    Takes a task DAG and produces an optimal schedule that minimizes
    total wall-clock time, respecting dependencies and worker limits.
    Tasks marked as DONE are skipped.

    Args:
        file: Path to a tasks.json file. If omitted, uses a sample DAG.
        num_workers: Number of parallel workers (e.g. concurrent Claude sessions). Default 1.
        hours_per_day: Hours of work per day, for calendar-day estimates. Optional.
    """
    _, dag = _load_dag(file)
    result = solve_schedule(
        dag,
        num_workers=num_workers,
        hours_per_day=hours_per_day,
    )
    return result.format()
