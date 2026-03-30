"""Rich-formatted usage reports."""

from __future__ import annotations

from datetime import date

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claude_flow.observer.store import UsageStore

SUBSCRIPTION_TIERS = {
    "Pro": {"price": 20, "value_estimate": 50},
    "Max (5x)": {"price": 100, "value_estimate": 200},
    "Max (20x)": {"price": 200, "value_estimate": 800},
}


class UsageReport:
    def __init__(self, store: UsageStore, console: Console | None = None) -> None:
        self.store = store
        self.console = console or Console()

    def print_full_report(self) -> None:
        self.console.print()
        self.console.rule("[bold cyan]ClaudeFlow Usage Report[/bold cyan]")
        self.console.print()
        self._print_summary()
        self._print_cost_by_model()
        self._print_projects()
        self._print_burn_rate()
        self._print_cost_vs_subscription()
        self._print_trends()
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

    def _print_cost_by_model(self) -> None:
        data = self.store.cost_by_model()
        if not data:
            return
        total_cost = self.store.total_cost() or 1.0

        table = Table(title="Cost by Model", border_style="bright_blue", show_edge=True)
        table.add_column("Model", style="bold")
        table.add_column("Sessions", justify="right", style="dim")
        table.add_column("Input Tokens", justify="right", style="green")
        table.add_column("Output Tokens", justify="right", style="yellow")
        table.add_column("Cost", justify="right", style="red")
        table.add_column("% of Total", justify="right")

        for row in data:
            pct = (row["cost_usd"] / total_cost) * 100
            bar = "█" * int(pct / 5)
            table.add_row(
                row["model"],
                f"{row['sessions']:,}",
                f"{row['input_tokens']:,}",
                f"{row['output_tokens']:,}",
                f"${row['cost_usd']:,.2f}",
                f"[cyan]{bar}[/cyan] {pct:.1f}%",
            )
        self.console.print(table)

    def _print_projects(self) -> None:
        projects = self.store.projects_with_cost(limit=20)
        if not projects:
            return
        total_cost = self.store.total_cost() or 1.0

        table = Table(
            title="Cost by Project (top 20)",
            border_style="bright_blue",
            show_edge=True,
        )
        table.add_column("#", justify="right", style="dim", width=4)
        table.add_column("Project", style="cyan")
        table.add_column("Sessions", justify="right", style="dim")
        table.add_column("Total Tokens", justify="right", style="yellow")
        table.add_column("Cost", justify="right", style="red")
        table.add_column("Share", justify="right")

        for i, row in enumerate(projects, 1):
            pct = (row["cost_usd"] / total_cost) * 100
            bar = "█" * max(1, int(pct / 3))
            display = row["project"]
            table.add_row(
                str(i),
                display,
                f"{row['sessions']:,}",
                f"{row['total_tokens']:,}",
                f"${row['cost_usd']:,.2f}",
                f"[green]{bar}[/green] {pct:.1f}%",
            )
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
        _, cost_per_hour = self.store.burn_rate()
        monthly_projected = cost_per_hour * 24 * 30

        table = Table(title="Monthly Cost vs Subscription Tiers", border_style="yellow")
        table.add_column("Tier", style="bold")
        table.add_column("Price", justify="right")
        table.add_column("API Equivalent", justify="right", style="dim")
        table.add_column("Your Projected", justify="right", style="red")
        table.add_column("ROI", justify="right")
        table.add_column("Verdict", justify="center")

        for tier, info in SUBSCRIPTION_TIERS.items():
            if info["value_estimate"] > 0 and monthly_projected > 0:
                ratio = info["value_estimate"] / monthly_projected
                eff_style = "green" if ratio >= 1 else "red"
                roi = f"[{eff_style}]{ratio:.1f}x[/{eff_style}]"
                if ratio >= 2:
                    verdict = "[bold green]Worth It[/bold green]"
                elif ratio >= 1:
                    verdict = "[green]Marginal[/green]"
                else:
                    verdict = "[bold red]Not Worth It[/bold red]"
            else:
                roi = "[dim]N/A[/dim]"
                verdict = "[dim]N/A[/dim]"
            table.add_row(
                tier,
                f"${info['price']}/mo",
                f"~${info['value_estimate']}/mo",
                f"${monthly_projected:,.2f}/mo",
                roi,
                verdict,
            )
        self.console.print(table)

    def _print_trends(self) -> None:
        self._print_daily_chart()
        self._print_hours()
        self._print_day_of_week()
        self._print_weekly()
        self._print_monthly()

    # ── vertical daily column chart ──────────────────────────────────────────

    _PARTIAL = " ▁▂▃▄▅▆▇█"  # index 0 = empty, 1–8 = eighths

    def _col_style(self, frac: float, is_weekend: bool) -> str:
        if is_weekend:
            return "magenta"
        if frac > 0.85:
            return "red"
        if frac > 0.6:
            return "yellow"
        if frac > 0.25:
            return "cyan"
        return "bright_black"

    def _print_daily_chart(self) -> None:
        days = self.store.usage_by_date(limit=60)
        if not days:
            return

        chart_height = 10  # rows of character cells
        values = [d["tokens"] for d in days]
        max_val = max(values) or 1
        fracs = [v / max_val for v in values]

        # Parse dates once for weekend detection and axis labels
        parsed: list[date] = []
        for d in days:
            try:
                parsed.append(date.fromisoformat(d["date"]))
            except (ValueError, TypeError):
                parsed.append(None)  # type: ignore[arg-type]

        # Build grid: rows × columns, each column = one bar char + one space
        # grid[row][col] = (char, style)
        grid: list[list[tuple[str, str]]] = [
            [(" ", "") for _ in days] for _ in range(chart_height)
        ]

        for col, (frac, dt) in enumerate(zip(fracs, parsed)):
            is_weekend = dt is not None and dt.weekday() >= 5
            style = self._col_style(frac, is_weekend)
            total_eighths = int(frac * chart_height * 8)
            full_rows = total_eighths // 8
            partial = total_eighths % 8
            for row in range(chart_height):
                # row 0 = bottom, chart_height-1 = top; render top-down so invert
                actual = chart_height - 1 - row
                if actual < full_rows:
                    grid[row][col] = ("█", style)
                elif actual == full_rows and partial:
                    grid[row][col] = (self._PARTIAL[partial], style)

        def _fmt_val(v: float) -> str:
            if v >= 1_000_000:
                return f"{v / 1_000_000:.1f}M"
            if v >= 1_000:
                return f"{v / 1_000:.0f}K"
            return str(int(v))

        # Y-axis: gridlines at 25%, 50%, 75%, 100% of max
        gridline_fracs = {1.0: max_val, 0.75: max_val * 0.75, 0.5: max_val * 0.5, 0.25: max_val * 0.25}
        # Map top-down row index → (label, is_gridline)
        row_meta: dict[int, tuple[str, bool]] = {}
        for gf, gval in gridline_fracs.items():
            level = int(gf * chart_height * 8)
            rb = min(level // 8, chart_height - 1)  # row from bottom
            td = chart_height - 1 - rb              # top-down index
            row_meta[td] = (_fmt_val(gval), True)

        y_width = max((len(lbl) for lbl, _ in row_meta.values()), default=4)

        self.console.print()
        self.console.rule("[bold]Daily Token Usage (last 60 days)[/bold]")
        self.console.print()

        for r, row in enumerate(grid):
            lbl, is_gl = row_meta.get(r, ("", False))
            label = lbl.rjust(y_width)
            line = Text(f"{label} │")
            for col, (ch, st) in enumerate(row):
                if ch != " ":
                    line.append(ch, style=st)
                elif is_gl:
                    line.append("·", style="bright_black")
                else:
                    line.append(" ")
                if col < len(row) - 1:
                    line.append(" ")
            self.console.print(line)

        # X-axis separator
        axis = " " * y_width + " └" + "──" * len(days)
        self.console.print(axis)

        # Date labels: show day number every 7 days, month name at month start
        label_row = Text(" " * (y_width + 2))
        prev_month = None
        for i, (d, dt) in enumerate(zip(days, parsed)):
            if dt is None:
                label_row.append("  ")
                continue
            if dt.month != prev_month:
                abbr = dt.strftime("%b")[:2]  # 2 chars to stay column-aligned
                label_row.append(abbr, style="bold cyan")
                prev_month = dt.month
            elif dt.day % 7 == 1:
                day_str = str(dt.day).rjust(2)
                label_row.append(day_str, style="dim")
            else:
                label_row.append("  ")
        self.console.print(label_row)
        self.console.print()

    # ── horizontal summary tables ─────────────────────────────────────────────

    def _trend_table(self, activity_width: int = 22) -> Table:
        t = Table(show_edge=True, border_style="bright_blue", show_header=True)
        t.add_column("Period", width=15, no_wrap=True)
        t.add_column("Activity", width=activity_width, no_wrap=True)
        t.add_column("Events", justify="right", style="dim", width=8)
        t.add_column("Tokens", justify="right", style="yellow", width=13)
        t.add_column("Cost", justify="right", style="red", width=10)
        return t

    def _bar(self, frac: float, width: int = 20, weekend: bool = False) -> Text:
        style = "magenta" if weekend else ("cyan" if frac < 0.6 else "yellow" if frac < 0.85 else "red")
        return Text("█" * int(frac * width), style=style)

    def _print_hours(self) -> None:
        hours = self.store.usage_by_hour()
        if not hours or all(h["cnt"] == 0 for h in hours):
            return
        max_tokens = max(h["tokens"] for h in hours) or 1

        def _hour_label(h: int) -> str:
            if h == 0:
                return "12 AM"
            if h < 12:
                return f" {h} AM"
            if h == 12:
                return "12 PM"
            return f" {h - 12} PM"

        self.console.print()
        self.console.rule("[bold]Usage by Hour of Day[/bold]")
        table = self._trend_table()
        for h in hours:
            frac = h["tokens"] / max_tokens
            table.add_row(_hour_label(h["hour"]), self._bar(frac), f"{h['cnt']:,}", f"{h['tokens']:,}", f"${h['cost']:.2f}")
        self.console.print(table)

    def _print_day_of_week(self) -> None:
        days = self.store.usage_by_day_of_week()
        if not days or all(d["cnt"] == 0 for d in days):
            return
        max_tokens = max(d["tokens"] for d in days) or 1

        self.console.print()
        self.console.rule("[bold]Usage by Day of Week[/bold]")
        table = self._trend_table()
        for d in days:
            frac = d["tokens"] / max_tokens
            is_weekend = d["day"] in ("Sat", "Sun")
            label = f"[bold]{d['day']}[/bold]" if is_weekend else d["day"]
            table.add_row(label, self._bar(frac, weekend=is_weekend), f"{d['cnt']:,}", f"{d['tokens']:,}", f"${d['cost']:.2f}")
        self.console.print(table)

    def _print_weekly(self) -> None:
        weeks = self.store.usage_by_week(limit=12)
        if not weeks:
            return
        weeks = list(reversed(weeks))  # chronological
        max_tokens = max(w["tokens"] for w in weeks) or 1

        self.console.print()
        self.console.rule("[bold]Weekly Trends (last 12 weeks)[/bold]")
        table = self._trend_table()
        for w in weeks:
            frac = w["tokens"] / max_tokens
            table.add_row(w["week"], self._bar(frac), f"{w['cnt']:,}", f"{w['tokens']:,}", f"${w['cost']:.2f}")
        self.console.print(table)

    def _print_monthly(self) -> None:
        months = self.store.usage_by_month()
        if not months:
            return
        max_tokens = max(m["tokens"] for m in months) or 1

        self.console.print()
        self.console.rule("[bold]Monthly Trends[/bold]")
        table = self._trend_table(activity_width=14)
        table.add_column("MoM", justify="right", width=7)

        prev_cost = None
        for m in months:
            frac = m["tokens"] / max_tokens
            if prev_cost and prev_cost > 0:
                change = ((m["cost"] - prev_cost) / prev_cost) * 100
                sign = "+" if change >= 0 else ""
                s = "red" if change > 20 else "green" if change < -10 else "dim"
                mom = f"[{s}]{sign}{change:.0f}%[/{s}]"
            else:
                mom = "[dim]—[/dim]"
            table.add_row(m["month"], self._bar(frac, width=12), f"{m['cnt']:,}", f"{m['tokens']:,}", f"${m['cost']:.2f}", mom)
            prev_cost = m["cost"]
        self.console.print(table)
