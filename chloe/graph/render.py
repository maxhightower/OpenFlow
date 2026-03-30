"""Rich terminal renderer for DAG visualization."""

from __future__ import annotations

from rich.console import Console, Group
from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from chloe.graph.engine import DAGEngine
from chloe.graph.models import (
    STATUS_ICONS,
    TYPE_STYLES,
    Task,
    TaskStatus,
)

NODE_INNER_WIDTH = 28


class DAGRenderer:
    """Render a DAG as a vertical top-to-bottom visual graph."""

    def __init__(self, engine: DAGEngine, console: Console | None = None) -> None:
        self.engine = engine
        self.console = console or Console()

    def render(self) -> None:
        self.console.print()
        levels = self.engine.topological_levels()
        if not levels:
            self.console.print("[dim]Empty DAG — nothing to render.[/dim]")
            return

        crit_path, crit_hours = self.engine.critical_path()
        crit_set = set(crit_path)
        crit_edges = set(zip(crit_path, crit_path[1:]))

        self._render_title()
        self._render_vertical_dag(levels, crit_set, crit_edges)
        self._render_summary(crit_path, crit_hours, levels)
        self._render_legend()
        self.console.print()

    # -- Title ---------------------------------------------------------------

    def _render_title(self) -> None:
        total = len(self.engine.tasks)
        done = sum(1 for t in self.engine.tasks.values() if t.status == TaskStatus.DONE)
        self.console.rule("[bold cyan]Dependency Graph[/bold cyan]")
        self.console.print(f"  [dim]{total} tasks, {done} completed[/dim]\n")

    # -- Vertical DAG --------------------------------------------------------

    def _render_vertical_dag(
        self,
        levels: list[list[str]],
        crit_set: set[str],
        crit_edges: set[tuple[str, str]],
    ) -> None:
        for li, level in enumerate(levels):
            # Level header
            self.console.print(f"  [dim]Level {li}[/dim]")

            # Render nodes in this level side-by-side
            node_panels = []
            for tid in level:
                task = self.engine.get_task(tid)
                on_crit = tid in crit_set
                node_panels.append(self._render_node_panel(task, on_crit))

            self.console.print(Columns(node_panels, padding=(0, 1), expand=False))

            # Draw connectors to next level
            if li < len(levels) - 1:
                self._render_connectors(level, levels[li + 1], crit_edges)

    def _render_node_panel(self, task: Task, on_critical: bool) -> Panel:
        """Render a task as a Rich Panel."""
        style_color, type_char = TYPE_STYLES.get(task.task_type, ("white", "?"))
        status_icon = STATUS_ICONS.get(task.status, "?")
        border = "bold red" if on_critical else style_color

        # Title line: type badge + task ID
        title = Text()
        title.append(f" {type_char} ", style=f"bold {style_color}")
        title.append(task.id, style="bold" if on_critical else "dim")

        # Body: status icon, name, hours
        body = Text()
        body.append_text(Text.from_markup(status_icon))
        body.append(f" {task.name}\n", style="bold" if on_critical else "")
        body.append(f"  {task.estimated_hours:.0f}h", style="dim")
        body.append(f"  {task.status.value}", style="dim italic")

        return Panel(
            body,
            title=title,
            border_style=border,
            width=NODE_INNER_WIDTH + 4,
            padding=(0, 1),
        )

    def _render_connectors(
        self,
        current_level: list[str],
        next_level: list[str],
        crit_edges: set[tuple[str, str]],
    ) -> None:
        """Draw arrows between levels showing which tasks feed into which."""
        # Collect edges from current level to next level
        edges: list[tuple[str, str, bool]] = []
        for src in current_level:
            for dst in next_level:
                if self.engine.graph.has_edge(src, dst):
                    is_crit = (src, dst) in crit_edges
                    edges.append((src, dst, is_crit))

        if not edges:
            self.console.print()
            return

        # Build position maps (center of each node panel)
        panel_width = NODE_INNER_WIDTH + 4 + 1  # panel width + gap
        src_positions = {tid: i * panel_width + panel_width // 2 for i, tid in enumerate(current_level)}
        dst_positions = {tid: i * panel_width + panel_width // 2 for i, tid in enumerate(next_level)}

        # Render connector lines
        # Line 1: vertical drops from sources
        # Line 2: horizontal span + verticals to destinations
        # Line 3: arrow tips into destinations

        max_pos = max(
            max(src_positions.values()),
            max(dst_positions.values()),
        ) + 2

        # Determine which columns have vertical lines
        line1 = [" "] * (max_pos + 1)
        line2 = [" "] * (max_pos + 1)
        line3 = [" "] * (max_pos + 1)

        # For simplicity, render each edge as a vertical drop
        # Group edges by destination to merge
        by_dst: dict[str, list[tuple[str, bool]]] = {}
        for src, dst, ic in edges:
            by_dst.setdefault(dst, []).append((src, ic))

        for dst, sources in by_dst.items():
            dp = dst_positions[dst]
            any_crit = any(ic for _, ic in sources)
            ch_vert = "║" if any_crit else "│"
            ch_arrow = "▼" if any_crit else "▽"

            for src, is_crit in sources:
                sp = src_positions[src]
                ch = "║" if is_crit else "│"
                line1[sp] = ch

                # Horizontal connector on line2
                lo, hi = min(sp, dp), max(sp, dp)
                for x in range(lo, hi + 1):
                    if line2[x] == " ":
                        line2[x] = "═" if any_crit else "─"
                    elif line2[x] in ("│", "║"):
                        line2[x] = "╪" if any_crit else "┼"

                # Corners/junctions
                if sp <= dp:
                    line2[sp] = "╚" if is_crit else "└"
                    if line2[dp] in ("═", "─", " "):
                        line2[dp] = "╗" if any_crit else "┐"
                    else:
                        line2[dp] = "╬" if any_crit else "┼"
                else:
                    line2[sp] = "╝" if is_crit else "┘"
                    if line2[dp] in ("═", "─", " "):
                        line2[dp] = "╔" if any_crit else "┌"
                    else:
                        line2[dp] = "╬" if any_crit else "┼"

            line3[dp] = ch_arrow

        # Print with styling
        self._print_connector_line(line1, crit_chars="║╪╬")
        self._print_connector_line(line2, crit_chars="═╚╗╝╔╬╪║")
        self._print_connector_line(line3, crit_chars="▼")
        self.console.print()

    def _print_connector_line(self, chars: list[str], crit_chars: str) -> None:
        line = Text("  ")  # indent
        for ch in chars:
            if ch in crit_chars:
                line.append(ch, style="bold red")
            elif ch != " ":
                line.append(ch, style="dim cyan")
            else:
                line.append(ch)
        # Strip trailing spaces
        self.console.print(line)

    # -- Summary panel -------------------------------------------------------

    def _render_summary(
        self, crit_path: list[str], crit_hours: float, levels: list[list[str]]
    ) -> None:
        path_str = Text()
        for i, tid in enumerate(crit_path):
            task = self.engine.get_task(tid)
            path_str.append(f"{task.name}", style="bold red")
            if i < len(crit_path) - 1:
                path_str.append(" → ", style="dim")

        max_parallel = max(len(lv) for lv in levels)

        self.console.print(
            Panel(
                Text.assemble(
                    ("Critical Path: ", "bold"),
                    path_str,
                    ("\n\nWall-clock estimate: ", ""),
                    (f"{crit_hours:.0f}h", "bold red"),
                    ("  |  Sequential steps: ", ""),
                    (f"{len(levels)}", "bold"),
                    ("  |  Max parallelism: ", ""),
                    (f"{max_parallel}", "bold green"),
                ),
                title="Analysis",
                border_style="cyan",
            )
        )

    # -- Legend ---------------------------------------------------------------

    def _render_legend(self) -> None:
        table = Table(title="Legend", show_edge=False, box=None, padding=(0, 2))
        table.add_column("Types", style="dim")
        table.add_column("Status", style="dim")

        type_str = Text()
        for tt, (color, char) in TYPE_STYLES.items():
            type_str.append(f"  {char}", style=f"bold {color}")
            type_str.append(f"={tt.value}", style="dim")

        status_str = Text()
        for ss, icon in STATUS_ICONS.items():
            status_str.append_text(Text.from_markup(f"  {icon}"))
            status_str.append(f"={ss.value}", style="dim")

        table.add_row(type_str, status_str)
        self.console.print(table)
