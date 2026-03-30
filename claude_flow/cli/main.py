"""ClaudeFlow CLI entry point."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel

from rich.table import Table
from rich.text import Text

from claude_flow.observer.parser import UsageParser
from claude_flow.observer.report import UsageReport
from claude_flow.observer.store import UsageStore
from claude_flow.graph.engine import DAGEngine
from claude_flow.graph.models import ProjectDAG
from claude_flow.graph.render import DAGRenderer
from claude_flow.graph.sample import build_sample_dag
from claude_flow.scheduler import SchedulerEngine
from claude_flow.scheduler.estimator import DEFAULT_TOKEN_ESTIMATES

app = typer.Typer(
    name="claudeflow",
    help="Resource optimization scheduler for Claude Code subscriptions.",
    no_args_is_help=True,
)

# Force UTF-8 on Windows so Unicode box-drawing and status icons render correctly
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

console = Console()


@app.command()
def observe(
    claude_dir: str = typer.Option(
        None,
        "--claude-dir",
        help="Path to .claude directory (default: ~/.claude)",
    ),
    db_path: str = typer.Option(
        None,
        "--db",
        help="Path to SQLite database (default: observer/usage.db)",
    ),
) -> None:
    """Scan Claude Code usage logs and print a baseline report."""
    parser = UsageParser(
        claude_dir=Path(claude_dir) if claude_dir else None,
    )
    store = UsageStore(db_path=Path(db_path) if db_path else None)

    console.print("[dim]Scanning for JSONL usage logs...[/dim]")
    files = parser.find_jsonl_files()
    console.print(f"[dim]Found {len(files)} log file(s)[/dim]")

    records = parser.parse_all()
    console.print(f"[dim]Parsed {len(records)} session(s)[/dim]")

    store.clear()
    store.ingest(records)

    report = UsageReport(store, console)
    report.print_full_report()
    store.close()


@app.command()
def status(
    db_path: str = typer.Option(
        None,
        "--db",
        help="Path to SQLite database (default: observer/usage.db)",
    ),
    refresh: int = typer.Option(30, "--refresh", "-r", help="Refresh interval in seconds"),
) -> None:
    """Show live session burn rate (refreshes every 30s)."""
    store = UsageStore(db_path=Path(db_path) if db_path else None)

    def build_panel() -> Panel:
        tokens_per_hour, cost_per_hour = store.burn_rate()
        input_tok, output_tok = store.total_tokens()
        sessions = store.session_count()
        total_cost = store.total_cost()
        return Panel(
            f"[bold cyan]Sessions:[/bold cyan] {sessions}\n"
            f"[bold green]Input tokens:[/bold green] {input_tok:,}\n"
            f"[bold yellow]Output tokens:[/bold yellow] {output_tok:,}\n"
            f"[bold]Burn rate:[/bold] {tokens_per_hour:,.0f} tokens/hr | ${cost_per_hour:,.4f}/hr\n"
            f"[bold red]Total cost:[/bold red] ${total_cost:,.2f}",
            title="ClaudeFlow Live Status",
            border_style="bright_blue",
            subtitle=f"Refreshing every {refresh}s | Ctrl+C to exit",
        )

    try:
        with Live(build_panel(), console=console, refresh_per_second=0.5) as live:
            while True:
                time.sleep(refresh)
                # Re-parse to pick up new data
                store.close()
                store = UsageStore(db_path=Path(db_path) if db_path else None)
                live.update(build_panel())
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")
    finally:
        store.close()


@app.command()
def graph(
    file: str = typer.Option(
        None,
        "--file",
        "-f",
        help="Path to a tasks.json file defining the DAG",
    ),
) -> None:
    """Visualize a project task dependency graph."""
    import json

    if file:
        with open(file) as fh:
            data = json.load(fh)
        dag = ProjectDAG.from_dict(data)
    else:
        dag = build_sample_dag()
        console.print("[dim]Showing sample project DAG (use --file to load your own)[/dim]")

    try:
        engine = DAGEngine.from_project_dag(dag)
        renderer = DAGRenderer(engine, console)
        renderer.render()
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")


@app.command()
def budget(
    file: str = typer.Option(None, "--file", "-f", help="Path to tasks.json"),
    db_path: str = typer.Option(None, "--db", help="Path to scheduler SQLite database"),
    token_budget: int = typer.Option(500_000, "--budget", "-b", help="Token budget per 5-hour window"),
) -> None:
    """Show current token budget window status."""
    import json as _json

    if file:
        with open(file) as fh:
            data = _json.load(fh)
        dag = ProjectDAG.from_dict(data)
    else:
        dag = build_sample_dag()

    engine_dag = DAGEngine.from_project_dag(dag)
    scheduler = SchedulerEngine.from_dag_engine(
        engine_dag,
        db_path=Path(db_path) if db_path else None,
        token_budget=token_budget,
    )
    summary = scheduler.window_summary()

    pct_tokens = summary["pct_tokens_used"]
    pct_time = summary["pct_time_elapsed"]
    token_bar = _progress_bar(pct_tokens / 100, width=30)
    time_bar = _progress_bar(pct_time / 100, width=30)

    console.print()
    console.print(Panel(
        f"[bold]Token budget:[/bold]  {token_bar} {pct_tokens:.1f}%\n"
        f"  [dim]{summary['tokens_used']:,} used / {summary['token_budget']:,} total"
        f"  ({summary['tokens_remaining']:,} remaining)[/dim]\n\n"
        f"[bold]Time elapsed:[/bold]  {time_bar} {pct_time:.1f}%\n"
        f"  [dim]{summary['elapsed_hours']:.2f}h elapsed / 5.00h window"
        f"  ({summary['remaining_hours']:.2f}h remaining)[/dim]\n\n"
        f"[bold]Cost this window:[/bold]  [red]${summary['total_cost_usd']:.4f}[/red]\n"
        f"[bold]Window started:[/bold]    [dim]{summary['started_at']}[/dim]\n"
        f"[bold]Window ends:[/bold]       [dim]{summary['ends_at']}[/dim]",
        title="[bold cyan]Budget Window[/bold cyan]",
        border_style="cyan",
    ))
    console.print()
    scheduler.store.close()


@app.command()
def estimate(
    task_type: str = typer.Option(None, "--type", "-t", help="Task type (feature, bug-fix, refactor, test, docs, release)"),
    model: str = typer.Option("claude-sonnet-4-6", "--model", "-m", help="Model name"),
    db_path: str = typer.Option(None, "--db", help="Path to scheduler SQLite database"),
) -> None:
    """Show token cost estimates for task types."""
    from claude_flow.scheduler.store import SchedulerStore
    from claude_flow.scheduler.estimator import CostEstimator
    from claude_flow.graph.models import TaskType

    store = SchedulerStore(db_path=Path(db_path) if db_path else None)
    estimator = CostEstimator(store)

    table = Table(title="Token Cost Estimates", border_style="cyan", show_edge=True)
    table.add_column("Task Type", style="bold")
    table.add_column("Model", style="dim")
    table.add_column("p50 (median)", justify="right")
    table.add_column("p90 (budget)", justify="right", style="yellow")
    table.add_column("Samples", justify="right", style="dim")
    table.add_column("Source", style="dim")

    types_to_show = [TaskType(task_type)] if task_type else list(TaskType)
    models_to_show = [model]

    for tt in types_to_show:
        for m in models_to_show:
            from claude_flow.graph.models import Task
            dummy = Task(id="_", name="_", task_type=tt)
            est = estimator.estimate(dummy, m)
            source = "learned" if est.sample_count >= 3 else "default"
            table.add_row(
                tt.value,
                m,
                f"{est.p50_tokens:,}",
                f"{est.p90_tokens:,}",
                str(est.sample_count),
                source,
            )

    console.print()
    console.print(table)
    console.print()
    store.close()


@app.command()
def schedule(
    file: str = typer.Option(None, "--file", "-f", help="Path to tasks.json"),
    token_budget: int = typer.Option(500_000, "--budget", "-b", help="Token budget per window"),
    model: str = typer.Option("claude-sonnet-4-6", "--model", "-m", help="Model to use"),
    db_path: str = typer.Option(None, "--db", help="Path to scheduler SQLite database"),
) -> None:
    """Show the optimized execution schedule for a task DAG."""
    import json

    if file:
        with open(file) as fh:
            data = json.load(fh)
        dag = ProjectDAG.from_dict(data)
    else:
        dag = build_sample_dag()
        console.print("[dim]Using sample DAG (pass --file tasks.json to use your own)[/dim]")

    engine_dag = DAGEngine.from_project_dag(dag)
    scheduler = SchedulerEngine.from_dag_engine(
        engine_dag,
        db_path=Path(db_path) if db_path else None,
        token_budget=token_budget,
        model=model,
    )

    plan = scheduler.build_schedule()
    summary = scheduler.window_summary()

    console.print()
    console.rule("[bold cyan]Execution Schedule[/bold cyan]")
    console.print(
        f"  [dim]DAG: {plan.dag_name} | "
        f"Window: {summary['tokens_remaining']:,} tokens remaining | "
        f"Model: {model}[/dim]\n"
    )

    if not plan.ordered_tasks:
        msg = plan.infeasibility_reason or "No runnable tasks"
        console.print(f"  [yellow]{msg}[/yellow]\n")
        scheduler.store.close()
        return

    table = Table(show_edge=True, border_style="bright_blue", show_header=True)
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Task ID", style="bold", width=8)
    table.add_column("Name")
    table.add_column("Type", style="dim", width=10)
    table.add_column("Est. Tokens", justify="right", style="yellow", width=12)
    table.add_column("Priority", justify="right", width=9)
    table.add_column("Scheduled", style="dim", width=10)

    for st in plan.ordered_tasks:
        task = engine_dag.get_task(st.task_id)
        table.add_row(
            str(st.priority_rank),
            task.id,
            task.name,
            task.task_type.value,
            f"{st.estimated_token_cost:,}",
            str(task.priority),
            st.scheduled_start.strftime("%H:%M:%S"),
        )

    console.print(table)
    console.print(
        f"\n  [bold]Total estimated:[/bold] "
        f"[yellow]{plan.total_estimated_tokens:,}[/yellow] tokens | "
        f"[red]${plan.total_estimated_cost_usd:.4f}[/red]"
    )
    if not plan.is_feasible:
        console.print(f"  [red]Warning:[/red] {plan.infeasibility_reason}")
    console.print()
    scheduler.store.close()


@app.command()
def run(
    file: str = typer.Option(None, "--file", "-f", help="Path to tasks.json"),
    task_id: str = typer.Option(None, "--task", "-t", help="Specific task ID to run"),
    token_budget: int = typer.Option(500_000, "--budget", "-b", help="Token budget per window"),
    model: str = typer.Option("claude-sonnet-4-6", "--model", "-m", help="Model to use"),
    db_path: str = typer.Option(None, "--db", help="Path to scheduler SQLite database"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would run without executing"),
) -> None:
    """Execute the next scheduled task (or a specific task) via claude CLI."""
    import asyncio
    import json
    from claude_flow.scheduler.models import AgentConfig
    from claude_flow.dispatcher.runner import TaskRunner, TaskRunError

    if file:
        with open(file) as fh:
            data = json.load(fh)
        dag = ProjectDAG.from_dict(data)
        agent_configs = {
            c["config_id"]: AgentConfig.from_dict(c)
            for c in data.get("agent_configs", [])
        }
    else:
        dag = build_sample_dag()
        agent_configs = {}
        console.print("[dim]Using sample DAG[/dim]")

    engine_dag = DAGEngine.from_project_dag(dag)
    scheduler = SchedulerEngine.from_dag_engine(
        engine_dag,
        db_path=Path(db_path) if db_path else None,
        token_budget=token_budget,
        model=model,
    )

    if task_id:
        task = engine_dag.get_task(task_id)
    else:
        task = scheduler.next_task()

    if task is None:
        console.print("[yellow]No runnable tasks available.[/yellow]")
        scheduler.store.close()
        return

    # Resolve or build a default AgentConfig
    config = agent_configs.get(task.agent_config_id or "")
    if config is None:
        config = AgentConfig(
            config_id="_default",
            prompt_template=(
                "Complete the following task:\n\n"
                "Task: {{ task.name }}\n"
                "Type: {{ task.task_type.value }}\n"
                "Estimated effort: {{ task.estimated_hours }}h\n"
            ),
            working_directory=str(Path.cwd()),
            model=model,
        )

    runner = TaskRunner(store=scheduler.store)
    args = runner.build_cli_args(task, config)
    prompt = runner.render_prompt(task, config)

    console.print()
    console.print(Panel(
        f"[bold]Task:[/bold]    {task.id} — {task.name}\n"
        f"[bold]Type:[/bold]    {task.task_type.value}\n"
        f"[bold]Model:[/bold]   {config.model}\n"
        f"[bold]Prompt:[/bold]  [dim]{prompt[:120]}{'...' if len(prompt) > 120 else ''}[/dim]\n"
        f"[bold]Command:[/bold] [dim]{' '.join(args[:6])} ...[/dim]",
        title="[bold cyan]Dispatching Task[/bold cyan]" + (" [yellow](dry run)[/yellow]" if dry_run else ""),
        border_style="cyan",
    ))

    if dry_run:
        console.print("\n[dim]Dry run — no task executed.[/dim]\n")
        scheduler.store.close()
        return

    window = scheduler.current_window()

    async def _run() -> None:
        try:
            with console.status(f"[cyan]Running {task.id}: {task.name}...[/cyan]"):
                task_run = await runner.run(task, config, window.window_id, dry_run=False)
            scheduler.mark_completed(task.id, task_run)
            console.print(
                f"\n  [green]✓ Completed[/green] {task.id} in "
                f"{(task_run.completed_at - task_run.started_at).seconds}s | "
                f"[yellow]{task_run.actual_total_tokens:,}[/yellow] tokens | "
                f"[red]${task_run.actual_cost_usd:.4f}[/red]\n"
            )
        except TaskRunError as e:
            console.print(f"\n  [red]✗ Failed[/red] {task.id}: {e}\n")
            scheduler.store.save_run(e.run)

    asyncio.run(_run())
    scheduler.store.close()


@app.command()
def queue(
    name: str = typer.Argument(..., help="Task name"),
    file: str = typer.Option(None, "--file", "-f", help="Path to tasks.json to update"),
    task_type: str = typer.Option("feature", "--type", "-t", help="Task type"),
    hours: float = typer.Option(1.0, "--hours", "-h", help="Estimated hours"),
    priority: int = typer.Option(5, "--priority", "-p", help="Priority 1-10 (1=highest)"),
    depends_on: str = typer.Option(None, "--depends-on", "-d", help="Comma-separated task IDs this depends on"),
    tokens: int = typer.Option(0, "--tokens", help="Estimated token cost (0 = use default)"),
) -> None:
    """Add a task to the DAG (updates tasks.json if --file given)."""
    import json
    from claude_flow.graph.models import Task, TaskType, TaskStatus

    new_task = Task(
        id=f"T{name[:4].upper().replace(' ', '')}",
        name=name,
        task_type=TaskType(task_type),
        estimated_hours=hours,
        status=TaskStatus.PENDING,
        priority=priority,
        estimated_tokens=tokens,
    )

    deps = [d.strip() for d in depends_on.split(",")] if depends_on else []

    if file:
        with open(file) as fh:
            data = json.load(fh)
        data["tasks"].append(new_task.to_dict())
        for dep in deps:
            data["dependencies"].append([dep, new_task.id])
        with open(file, "w") as fh:
            json.dump(data, fh, indent=2)
        console.print(f"[green]Added[/green] task [bold]{new_task.id}[/bold] to {file}")
    else:
        console.print(
            Panel(
                f"[bold]ID:[/bold]       {new_task.id}\n"
                f"[bold]Name:[/bold]     {name}\n"
                f"[bold]Type:[/bold]     {task_type}\n"
                f"[bold]Priority:[/bold] {priority}\n"
                f"[bold]Depends on:[/bold] {deps or 'none'}\n\n"
                f"[dim]Pass --file tasks.json to persist to disk.[/dim]",
                title="[cyan]New Task (preview)[/cyan]",
                border_style="cyan",
            )
        )


@app.command()
def history(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of runs to show"),
    db_path: str = typer.Option(None, "--db", help="Path to scheduler SQLite database"),
) -> None:
    """Show recent task run history."""
    from claude_flow.scheduler.store import SchedulerStore
    from claude_flow.scheduler.models import RunStatus

    store = SchedulerStore(db_path=Path(db_path) if db_path else None)
    runs = store.get_recent_runs(limit=limit)

    if not runs:
        console.print("[dim]No runs recorded yet.[/dim]")
        store.close()
        return

    table = Table(title=f"Recent Runs (last {limit})", border_style="bright_blue", show_edge=True)
    table.add_column("Task", style="bold", width=8)
    table.add_column("Status", width=10)
    table.add_column("Model", style="dim", width=16)
    table.add_column("Input tok", justify="right", width=10)
    table.add_column("Output tok", justify="right", width=11)
    table.add_column("Cost", justify="right", style="red", width=8)
    table.add_column("Started", style="dim", width=20)

    STATUS_STYLE = {
        RunStatus.COMPLETED: "[green]✓ done[/green]",
        RunStatus.FAILED: "[red]✗ failed[/red]",
        RunStatus.RUNNING: "[yellow]⟳ running[/yellow]",
        RunStatus.CANCELLED: "[dim]cancelled[/dim]",
    }

    for r in runs:
        table.add_row(
            r.task_id,
            STATUS_STYLE.get(r.status, r.status.value),
            r.model,
            f"{r.actual_input_tokens:,}",
            f"{r.actual_output_tokens:,}",
            f"${r.actual_cost_usd:.4f}",
            r.started_at.strftime("%Y-%m-%d %H:%M:%S"),
        )

    console.print()
    console.print(table)
    console.print()
    store.close()


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-H", help="Bind address"),
    port: int = typer.Option(8420, "--port", "-p", help="Port number"),
    token_budget: int = typer.Option(500_000, "--budget", "-b", help="Token budget per window"),
    model: str = typer.Option("claude-sonnet-4-6", "--model", "-m", help="Model to use"),
    db_path: str = typer.Option(None, "--db", help="Path to SQLite database"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev mode)"),
) -> None:
    """Start the ClaudeFlow web dashboard and API server."""
    try:
        import uvicorn
        from claude_flow.web.config import WebConfig
    except ImportError:
        console.print(
            "[red]Web dependencies not installed.[/red]\n"
            "Install with: [bold]uv sync --extra web[/bold]"
        )
        raise typer.Exit(1)

    config = WebConfig(
        host=host,
        port=port,
        token_budget=token_budget,
        model=model,
        db_path=Path(db_path) if db_path else WebConfig().db_path,
    )

    console.print(f"\n  [bold cyan]ClaudeFlow Web[/bold cyan] starting on [bold]http://{host}:{port}[/bold]")
    console.print(f"  [dim]API docs: http://{host}:{port}/docs[/dim]")
    console.print(f"  [dim]Database: {config.db_path}[/dim]\n")

    # Store config in env so app.py can pick it up
    import os
    os.environ["CLAUDEFLOW_HOST"] = host
    os.environ["CLAUDEFLOW_PORT"] = str(port)
    os.environ["CLAUDEFLOW_TOKEN_BUDGET"] = str(token_budget)
    os.environ["CLAUDEFLOW_MODEL"] = model
    if db_path:
        os.environ["CLAUDEFLOW_DB"] = db_path

    uvicorn.run(
        "claude_flow.web.app:create_app",
        host=host,
        port=port,
        reload=reload,
        factory=True,
    )


@app.command(name="mcp-serve")
def mcp_serve(
    file: str = typer.Option(None, "--file", "-f", help="Path to tasks.json"),
    token_budget: int = typer.Option(500_000, "--budget", "-b", help="Token budget per window"),
    model: str = typer.Option("claude-sonnet-4-6", "--model", "-m", help="Model to use"),
    db_path: str = typer.Option(None, "--db", help="Path to scheduler SQLite database"),
) -> None:
    """Start the ClaudeFlow MCP server (stdio transport for Claude Code)."""
    import asyncio
    from claude_flow.mcp.server import run_server

    asyncio.run(run_server(
        dag_file=Path(file) if file else None,
        db_path=Path(db_path) if db_path else None,
        token_budget=token_budget,
        model=model,
    ))


def _progress_bar(fraction: float, width: int = 20) -> str:
    filled = int(fraction * width)
    empty = width - filled
    color = "green" if fraction < 0.7 else "yellow" if fraction < 0.9 else "red"
    return f"[{color}]{'█' * filled}[/{color}][dim]{'░' * empty}[/dim]"


if __name__ == "__main__":
    app()
