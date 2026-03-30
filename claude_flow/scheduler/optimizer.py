"""OR-Tools CP-SAT optimizer: schedule tasks within a token budget window."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from ortools.sat.python import cp_model

from claude_flow.graph.models import Task
from claude_flow.scheduler.models import BudgetWindow, ScheduledTask

# How many seconds to assume per 1000 tokens (rough wall-clock estimate)
SECONDS_PER_1K_TOKENS = 10

# Solver time limit in seconds
SOLVER_TIME_LIMIT = 10.0


class BudgetOptimizer:
    """
    Wraps OR-Tools CP-SAT to solve the budget-constrained scheduling problem.

    Formulation:
    - For each task T: boolean run[T] (include in this window?)
    - Maximize sum(priority_weight[T] * run[T])
    - Subject to:
        - sum(token_cost[T] * run[T]) <= tokens_remaining
        - Dependency precedence: if T2 depends on T1 and both run,
          start[T2] >= start[T1] + duration[T1]
        - start[T] + duration[T] <= window_remaining_seconds
    """

    def __init__(self, time_limit_seconds: float = SOLVER_TIME_LIMIT) -> None:
        self.time_limit_seconds = time_limit_seconds

    def solve(
        self,
        tasks: list[Task],
        token_estimates: dict[str, int],
        dependencies: list[tuple[str, str]],
        window: BudgetWindow,
    ) -> list[ScheduledTask]:
        """
        Returns ScheduledTask list ordered by priority_rank.
        Falls back to greedy if the solver finds no solution.
        """
        if not tasks:
            return []

        tokens_remaining = window.tokens_remaining
        remaining_seconds = int(window.remaining_seconds)

        if tokens_remaining <= 0 or remaining_seconds <= 0:
            return []

        result = self._solve_cp_sat(
            tasks, token_estimates, dependencies, tokens_remaining, remaining_seconds, window
        )

        if result is None:
            result = self._greedy_fallback(
                tasks, token_estimates, dependencies, tokens_remaining, remaining_seconds, window
            )

        return result

    # -- CP-SAT ----------------------------------------------------------------

    def _solve_cp_sat(
        self,
        tasks: list[Task],
        token_estimates: dict[str, int],
        dependencies: list[tuple[str, str]],
        tokens_remaining: int,
        remaining_seconds: int,
        window: BudgetWindow,
    ) -> list[ScheduledTask] | None:
        model = cp_model.CpModel()
        task_ids = [t.id for t in tasks]
        task_map = {t.id: t for t in tasks}
        now = datetime.now(timezone.utc)

        # Scale tokens to avoid solver precision issues (work in units of 100 tokens)
        scale = 100
        budget_scaled = tokens_remaining // scale

        estimates_scaled = {
            tid: max(1, token_estimates.get(tid, 1000) // scale)
            for tid in task_ids
        }

        # Duration per task in seconds
        durations = {
            tid: max(1, (token_estimates.get(tid, 1000) * SECONDS_PER_1K_TOKENS) // 1000)
            for tid in task_ids
        }

        # Decision variables
        run_vars = {tid: model.new_bool_var(f"run_{tid}") for tid in task_ids}
        start_vars = {
            tid: model.new_int_var(0, remaining_seconds, f"start_{tid}")
            for tid in task_ids
        }

        # Budget constraint
        model.add(
            sum(estimates_scaled[tid] * run_vars[tid] for tid in task_ids) <= budget_scaled
        )

        # Time window + dependency constraints
        dep_set = set(dependencies)
        for tid in task_ids:
            dur = durations[tid]
            # Must finish within window if it runs
            end_expr = start_vars[tid] + dur
            model.add(end_expr <= remaining_seconds).only_enforce_if(run_vars[tid])

        for src, dst in dep_set:
            if src in task_ids and dst in task_ids:
                # dst starts after src finishes (only if both run)
                both_run = model.new_bool_var(f"both_{src}_{dst}")
                model.add_bool_and([run_vars[src], run_vars[dst]]).only_enforce_if(both_run)
                model.add(
                    start_vars[dst] >= start_vars[src] + durations[src]
                ).only_enforce_if(both_run)
                # If src doesn't run, dst can't run either
                model.add_implication(run_vars[dst], run_vars[src])

        # Objective: maximize priority-weighted inclusion
        # priority 1 = highest weight, 10 = lowest
        weights = {
            tid: (11 - task_map[tid].priority) * 1000
            for tid in task_ids
        }
        model.maximize(sum(weights[tid] * run_vars[tid] for tid in task_ids))

        # Solve
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit_seconds
        status = solver.solve(model)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None

        # Extract solution
        selected = [
            tid for tid in task_ids
            if solver.value(run_vars[tid]) == 1
        ]
        if not selected:
            return None

        scheduled = []
        for rank, tid in enumerate(
            sorted(selected, key=lambda t: solver.value(start_vars[t])), start=1
        ):
            start_offset = solver.value(start_vars[tid])
            scheduled.append(ScheduledTask(
                task_id=tid,
                scheduled_start=now + timedelta(seconds=start_offset),
                estimated_token_cost=token_estimates.get(tid, 0),
                priority_rank=rank,
                window_id=window.window_id,
            ))

        return scheduled

    # -- Greedy fallback -------------------------------------------------------

    def _greedy_fallback(
        self,
        tasks: list[Task],
        token_estimates: dict[str, int],
        dependencies: list[tuple[str, str]],
        tokens_remaining: int,
        remaining_seconds: int,
        window: BudgetWindow,
    ) -> list[ScheduledTask]:
        """
        Simple greedy: topological order, skip tasks that exceed remaining budget.
        """
        task_map = {t.id: t for t in tasks}
        dep_set: dict[str, set[str]] = {t.id: set() for t in tasks}
        for src, dst in dependencies:
            if dst in dep_set:
                dep_set[dst].add(src)

        # Kahn's topological sort with priority tie-breaking
        in_degree = {tid: len(deps) for tid, deps in dep_set.items()}
        ready = sorted(
            [tid for tid, deg in in_degree.items() if deg == 0],
            key=lambda tid: task_map[tid].priority,
        )

        order: list[str] = []
        while ready:
            tid = ready.pop(0)
            order.append(tid)
            for other_id in task_map:
                if tid in dep_set.get(other_id, set()):
                    in_degree[other_id] -= 1
                    if in_degree[other_id] == 0:
                        ready.append(other_id)
                        ready.sort(key=lambda t: task_map[t].priority)

        now = datetime.now(timezone.utc)
        budget_left = tokens_remaining
        time_offset = 0
        scheduled = []
        rank = 1

        for tid in order:
            cost = token_estimates.get(tid, 0)
            dur = max(1, (cost * SECONDS_PER_1K_TOKENS) // 1000)
            if cost > budget_left:
                continue
            if time_offset + dur > remaining_seconds:
                continue
            scheduled.append(ScheduledTask(
                task_id=tid,
                scheduled_start=now + timedelta(seconds=time_offset),
                estimated_token_cost=cost,
                priority_rank=rank,
                window_id=window.window_id,
            ))
            budget_left -= cost
            time_offset += dur
            rank += 1

        return scheduled
