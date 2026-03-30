"""Schedule tasks using OR-Tools CP-SAT constraint solver."""

from __future__ import annotations

from dataclasses import dataclass, field

try:
    from ortools.sat.python import cp_model
except ImportError:
    cp_model = None  # type: ignore[assignment]

from claude_flow.graph.engine import DAGEngine
from claude_flow.graph.models import ProjectDAG, Task, TaskStatus


@dataclass
class ScheduledTask:
    """A task placed on the timeline by the solver."""

    task: Task
    start: int          # start time in hours from t=0
    end: int            # end time in hours from t=0
    worker: int         # assigned worker slot (0-indexed)

    @property
    def duration(self) -> int:
        return self.end - self.start


@dataclass
class Schedule:
    """Complete schedule produced by the solver."""

    tasks: list[ScheduledTask]
    makespan: int                           # total wall-clock hours
    num_workers: int
    hours_per_day: int | None = None        # None = continuous

    @property
    def total_work_hours(self) -> float:
        return sum(st.duration for st in self.tasks)

    @property
    def efficiency(self) -> float:
        """Ratio of work hours to available slot-hours."""
        if self.makespan == 0 or self.num_workers == 0:
            return 0.0
        return self.total_work_hours / (self.makespan * self.num_workers)

    @property
    def calendar_days(self) -> float | None:
        """Approximate calendar days if hours_per_day is set."""
        if self.hours_per_day:
            return self.makespan / self.hours_per_day
        return None

    def format(self) -> str:
        """Return a plain-text report of the schedule."""
        lines = [
            "=== Schedule ===",
            "",
            f"Makespan: {self.makespan}h wall-clock",
            f"Workers: {self.num_workers}",
            f"Total work: {self.total_work_hours}h",
            f"Efficiency: {self.efficiency:.0%}",
        ]
        if self.calendar_days is not None:
            lines.append(f"Calendar days (~{self.hours_per_day}h/day): {self.calendar_days:.1f}")

        lines += ["", "--- Timeline ---"]

        by_start = sorted(self.tasks, key=lambda st: (st.start, st.worker))
        for st in by_start:
            lines.append(
                f"  t={st.start:>3}-{st.end:<3}  "
                f"[worker {st.worker}]  "
                f"{st.task.name} ({st.task.estimated_hours}h, {st.task.task_type.value})"
            )

        lines += ["", "--- Worker Lanes ---"]
        for w in range(self.num_workers):
            worker_tasks = sorted(
                [st for st in self.tasks if st.worker == w],
                key=lambda st: st.start,
            )
            lane = " -> ".join(f"{st.task.name}[{st.start}-{st.end}]" for st in worker_tasks)
            lines.append(f"  Worker {w}: {lane}")

        return "\n".join(lines)


def solve(
    dag: ProjectDAG,
    num_workers: int = 1,
    hours_per_day: int | None = None,
    skip_done: bool = True,
) -> Schedule:
    """Solve for an optimal schedule minimizing makespan.

    Args:
        dag: The project task graph.
        num_workers: Number of parallel worker slots (e.g. concurrent sessions).
        hours_per_day: Optional daily hour budget. Used for calendar-day estimates
                       but does not partition the timeline into days.
        skip_done: If True, exclude tasks with status DONE.

    Returns:
        A Schedule with task placements and makespan.

    Raises:
        ValueError: If the DAG has cycles or the solver finds no solution.
    """
    if cp_model is None:
        raise ImportError(
            "OR-Tools is required for schedule optimization. "
            "Install it with: pip install claudeflow[solver]"
        )

    engine = DAGEngine.from_project_dag(dag)

    # Filter tasks
    tasks = [
        t for t in dag.tasks
        if not (skip_done and t.status == TaskStatus.DONE)
    ]

    if not tasks:
        return Schedule(tasks=[], makespan=0, num_workers=num_workers,
                        hours_per_day=hours_per_day)

    task_map = {t.id: t for t in tasks}
    task_ids = set(task_map.keys())

    # Use integer hours; convert fractional hours to integer (ceiling)
    durations = {
        t.id: max(1, int(t.estimated_hours + 0.5))
        for t in tasks
    }

    # Upper bound on horizon: sum of all durations (fully sequential)
    horizon = sum(durations.values())

    # -- Build CP-SAT model --------------------------------------------------
    model = cp_model.CpModel()

    starts: dict[str, cp_model.IntVar] = {}
    ends: dict[str, cp_model.IntVar] = {}
    intervals: dict[str, cp_model.IntervalVar] = {}

    for tid, dur in durations.items():
        s = model.new_int_var(0, horizon, f"start_{tid}")
        e = model.new_int_var(0, horizon, f"end_{tid}")
        iv = model.new_interval_var(s, dur, e, f"interval_{tid}")
        starts[tid] = s
        ends[tid] = e
        intervals[tid] = iv

    # Dependency constraints: predecessor must end before successor starts
    for from_id, to_id in dag.dependencies:
        if from_id in task_ids and to_id in task_ids:
            model.add(ends[from_id] <= starts[to_id])

    # Worker capacity: at most num_workers tasks running at any time
    demands = [1] * len(tasks)
    model.add_cumulative(
        [intervals[tid] for tid in durations],
        demands,
        num_workers,
    )

    # Objective: minimize makespan
    makespan = model.new_int_var(0, horizon, "makespan")
    model.add_max_equality(makespan, list(ends.values()))
    model.minimize(makespan)

    # -- Solve ---------------------------------------------------------------
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30.0
    status = solver.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise ValueError(
            f"Solver failed with status: {solver.status_name(status)}"
        )

    # -- Extract solution & assign workers -----------------------------------
    raw: list[tuple[str, int, int]] = []
    for tid in durations:
        raw.append((tid, solver.value(starts[tid]), solver.value(ends[tid])))

    # Greedy worker assignment: assign each task to the first available worker
    raw.sort(key=lambda x: (x[1], x[2]))
    worker_free_at = [0] * num_workers
    scheduled: list[ScheduledTask] = []

    for tid, start, end in raw:
        # Pick the worker that becomes free earliest (and is free by start)
        best_w = min(range(num_workers), key=lambda w: worker_free_at[w])
        scheduled.append(ScheduledTask(
            task=task_map[tid],
            start=start,
            end=end,
            worker=best_w,
        ))
        worker_free_at[best_w] = end

    return Schedule(
        tasks=scheduled,
        makespan=solver.value(makespan),
        num_workers=num_workers,
        hours_per_day=hours_per_day,
    )
