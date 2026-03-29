"""ClaudeFlow CLI entry point."""

from __future__ import annotations

import time
from pathlib import Path

import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel

from claude_flow.observer.parser import UsageParser
from claude_flow.observer.report import UsageReport
from claude_flow.observer.store import UsageStore

app = typer.Typer(
    name="claudeflow",
    help="Resource optimization scheduler for Claude Code subscriptions.",
    no_args_is_help=True,
)
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


if __name__ == "__main__":
    app()
