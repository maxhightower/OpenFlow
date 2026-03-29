"""Rich-formatted usage reports."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claude_flow.observer.store import UsageStore

# Subscription tiers (approximate monthly API-equivalent value)
SUBSCRIPTION_TIERS = {
    "Pro": {"price": 20, "value_estimate": 50},
    "Max (5x)": {"price": 100, "value_estimate": 200},
    "Max (20x)": {"price": 200, "value_estimate": 800},
}


class UsageReport:
    """Generate Rich-formatted reports from stored usage data."""

    def __init__(self, store: UsageStore, console: Console | None = None) -> None:
        self.store = store
        self.console = console or Console()

    def print_full_report(self) -> None:
        self.console.print()
        self.console.rule("[bold cyan]ClaudeFlow Usage Report[/bold cyan]")
        self.console.print()
        self._print_summary()
        self._print_peak_hours()
        self._print_avg_tokens_by_model()
        self._print_burn_rate()
        self._print_cost_vs_subscription()
        self._print_top_projects()
        self.console.print()

    def _print_summary(self) -> None:
        sessions = self.store.session_count()
        input_tok, output_tok = self.store.total_tokens()
        total_cost = self.store.total_cost()
        self.console.print(
            Panel(
                f"[bold]{sessions}[/bold] sessions parsed  |  "
                f"[green]{input_tok:,}[/green] input tokens  |  "
                f"[yellow]{output_tok:,}[/yellow] output tokens  |  "
                f"[red]${total_cost:,.2f}[/red] estimated cost",
                title="Summary",
                border_style="bright_blue",
            )
        )

    def _print_peak_hours(self) -> None:
        hours = self.store.peak_usage_hours()
        if not hours:
            return
        max_count = max(c for _, c in hours) if hours else 1
        self.console.print("\n[bold]Peak Usage Hours[/bold]")
        for hour, count in hours:
            bar_len = int((count / max_count) * 30)
            bar = Text("█" * bar_len, style="cyan")
            self.console.print(f"  {hour:02d}:00  {bar}  {count}")

    def _print_avg_tokens_by_model(self) -> None:
        data = self.store.avg_tokens_by_model()
        if not data:
            return
        table = Table(title="Average Tokens per Session by Model")
        table.add_column("Model", style="bold")
        table.add_column("Avg Input", justify="right", style="green")
        table.add_column("Avg Output", justify="right", style="yellow")
        for model, avg_in, avg_out in data:
            table.add_row(model, f"{avg_in:,.0f}", f"{avg_out:,.0f}")
        self.console.print(table)

    def _print_burn_rate(self) -> None:
        tokens_per_hour, cost_per_hour = self.store.burn_rate()
        self.console.print(
            Panel(
                f"[bold]{tokens_per_hour:,.0f}[/bold] tokens/hour  |  "
                f"[red]${cost_per_hour:,.4f}[/red]/hour  |  "
                f"[red]${cost_per_hour * 24:,.2f}[/red]/day (projected)",
                title="Burn Rate",
                border_style="yellow",
            )
        )

    def _print_cost_vs_subscription(self) -> None:
        total_cost = self.store.total_cost()
        _, cost_per_hour = self.store.burn_rate()
        monthly_projected = cost_per_hour * 24 * 30

        table = Table(title="Estimated Monthly Cost vs Subscription Tiers")
        table.add_column("Tier", style="bold")
        table.add_column("Price", justify="right")
        table.add_column("API Value", justify="right", style="dim")
        table.add_column("Your Projected", justify="right", style="red")
        table.add_column("Efficiency", justify="right")

        for tier, info in SUBSCRIPTION_TIERS.items():
            if info["value_estimate"] > 0 and monthly_projected > 0:
                ratio = info["value_estimate"] / monthly_projected
                eff_style = "green" if ratio >= 1 else "red"
                efficiency = f"[{eff_style}]{ratio:.1f}x[/{eff_style}]"
            else:
                efficiency = "N/A"
            table.add_row(
                tier,
                f"${info['price']}/mo",
                f"~${info['value_estimate']}/mo",
                f"${monthly_projected:,.2f}/mo",
                efficiency,
            )
        self.console.print(table)

    def _print_top_projects(self) -> None:
        projects = self.store.top_projects(3)
        if not projects:
            return
        table = Table(title="Top 3 Most Token-Heavy Projects")
        table.add_column("Rank", justify="center", style="bold")
        table.add_column("Project", style="cyan")
        table.add_column("Total Tokens", justify="right", style="yellow")
        for i, (project, tokens) in enumerate(projects, 1):
            table.add_row(str(i), project, f"{tokens:,}")
        self.console.print(table)
