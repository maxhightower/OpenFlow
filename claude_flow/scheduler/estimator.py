"""Token cost estimator: predicts token usage from historical runs."""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from claude_flow.graph.models import Task, TaskType
from claude_flow.scheduler.models import (
    CostEstimate,
    RateLimitEvent,
    TaskRun,
    WarningLevel,
    WindowEstimate,
)
from claude_flow.scheduler.store import SchedulerStore

if TYPE_CHECKING:
    from claude_flow.observer.store import UsageStore

WINDOW_SECONDS = 5 * 60 * 60  # 5-hour subscription window

# Default p50 token estimates per task type (no history available)
DEFAULT_TOKEN_ESTIMATES: dict[str, int] = {
    TaskType.BUG_FIX.value: 15_000,
    TaskType.FEATURE.value: 25_000,
    TaskType.REFACTOR.value: 20_000,
    TaskType.TEST.value: 12_000,
    TaskType.DOCS.value: 8_000,
    TaskType.RELEASE.value: 5_000,
}

# p90 is p50 * this multiplier when using defaults
DEFAULT_P90_MULTIPLIER = 1.5

MIN_SAMPLES_FOR_DISTRIBUTION = 3
MAX_SAMPLES_STORED = 50


class CostEstimator:
    def __init__(self, store: SchedulerStore) -> None:
        self.store = store

    def estimate(self, task: Task, model: str) -> CostEstimate:
        """Return p50/p90 token estimates for this task + model combination."""
        # If the task has an explicit override, use it directly
        if task.estimated_tokens > 0:
            p50 = task.estimated_tokens
            p90 = int(p50 * DEFAULT_P90_MULTIPLIER)
            return CostEstimate(
                task_type=task.task_type.value,
                model=model,
                p50_tokens=p50,
                p90_tokens=p90,
                sample_count=0,
                last_updated=datetime.now(timezone.utc),
            )

        existing = self.store.get_cost_estimate(task.task_type.value, model)
        if existing and existing.sample_count >= MIN_SAMPLES_FOR_DISTRIBUTION:
            return existing

        # Fall back to defaults
        p50 = DEFAULT_TOKEN_ESTIMATES.get(task.task_type.value, 20_000)
        p90 = int(p50 * DEFAULT_P90_MULTIPLIER)
        return CostEstimate(
            task_type=task.task_type.value,
            model=model,
            p50_tokens=p50,
            p90_tokens=p90,
            sample_count=existing.sample_count if existing else 0,
            last_updated=datetime.now(timezone.utc),
        )

    def estimate_tokens_for_schedule(
        self, tasks: list[Task], model: str
    ) -> dict[str, int]:
        """Return {task_id: estimated_tokens} using p90 for conservative scheduling."""
        return {
            task.id: self.estimate(task, model).p90_tokens
            for task in tasks
        }

    def update(self, task: Task, run: TaskRun) -> None:
        """Update learned distribution after a completed run."""
        actual = run.actual_total_tokens
        if actual == 0:
            return

        existing = self.store.get_cost_estimate(task.task_type.value, run.model)
        samples = self.store.get_task_samples(task.task_type.value, run.model, MAX_SAMPLES_STORED)

        # Include the new sample
        if actual not in samples:
            samples = [actual] + samples
        samples = samples[:MAX_SAMPLES_STORED]

        updated = _compute_estimate(
            task_type=task.task_type.value,
            model=run.model,
            samples=samples,
            existing=existing,
        )
        self.store.upsert_cost_estimate(updated)

    def all_estimates(self) -> list[dict]:
        """Return all known estimates as dicts for display."""
        results = []
        for task_type in TaskType:
            for model in ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5-20251001"]:
                est = self.store.get_cost_estimate(task_type.value, model)
                if est:
                    results.append(est.to_dict())
        return results


class WindowCapEstimator:
    """
    Learns the 5-hour subscription cap from observed rate-limit events.

    On first use there is no data — estimates are marked UNKNOWN.
    After each rate-limit hit, the inferred cap is updated as the median
    of observed `tokens_consumed` values. The median is conservative: the
    real cap is at least that high, likely slightly higher, but this
    ensures the scheduler never over-commits.

    Over time the estimate converges as more rate-limit events accumulate.
    The optimizer benefits automatically because BudgetWindow.tokens_remaining
    is fed from the estimate without any optimizer changes.
    """

    def __init__(
        self,
        store: SchedulerStore,
        usage_store: "UsageStore | None" = None,
    ) -> None:
        self.store = store
        self.usage_store = usage_store

    def record_rate_limit(
        self, tokens_consumed: int, retry_after_seconds: int = 0
    ) -> RateLimitEvent:
        """
        Call this whenever Claude Code returns a rate-limit error.

        tokens_consumed: total tokens used in the current window at time of error.
        retry_after_seconds: the wait time from the error response (0 if unknown).
        """
        now = datetime.now(timezone.utc)
        # Back-calculate when the window started
        window_start = now - timedelta(seconds=WINDOW_SECONDS - retry_after_seconds)
        event = RateLimitEvent(
            event_id=str(uuid.uuid4()),
            occurred_at=now,
            tokens_consumed=tokens_consumed,
            retry_after_seconds=retry_after_seconds,
            window_start_estimate=window_start,
        )
        self.store.save_rate_limit_event(event)
        return event

    def inferred_cap(self) -> int | None:
        """
        Return median tokens_consumed across all rate-limit events, or None.
        Uses median rather than minimum so occasional outliers don't corrupt
        the estimate — the real cap is stable; variance comes from measurement.
        """
        events = self.store.get_rate_limit_events(50)
        if not events:
            return None
        values = sorted(e.tokens_consumed for e in events)
        n = len(values)
        mid = n // 2
        if n % 2 == 0:
            return (values[mid - 1] + values[mid]) // 2
        return values[mid]

    def estimate(self, tokens_used: int, tokens_per_hour: float) -> WindowEstimate:
        """
        Return a WindowEstimate given current consumption and burn rate.
        Pure function — does not query any store.
        """
        events = self.store.get_rate_limit_events(50)
        sample_count = len(events)
        cap = self.inferred_cap()

        if cap is None:
            return WindowEstimate(
                inferred_cap=None,
                tokens_used=tokens_used,
                tokens_remaining=None,
                fraction_used=None,
                tokens_per_hour=tokens_per_hour,
                minutes_to_limit=None,
                warning_level=WarningLevel.UNKNOWN,
                sample_count=0,
            )

        tokens_remaining = max(0, cap - tokens_used)
        fraction_used = min(1.0, tokens_used / cap)

        if tokens_per_hour > 0 and tokens_remaining > 0:
            minutes_to_limit = (tokens_remaining / tokens_per_hour) * 60.0
        elif tokens_remaining == 0:
            minutes_to_limit = 0.0
        else:
            minutes_to_limit = None

        if fraction_used >= 0.90 or (minutes_to_limit is not None and minutes_to_limit < 15):
            level = WarningLevel.CRITICAL
        elif fraction_used >= 0.80 or (minutes_to_limit is not None and minutes_to_limit < 45):
            level = WarningLevel.WARNING
        elif fraction_used >= 0.60 or (minutes_to_limit is not None and minutes_to_limit < 90):
            level = WarningLevel.CAUTION
        else:
            level = WarningLevel.OK

        return WindowEstimate(
            inferred_cap=cap,
            tokens_used=tokens_used,
            tokens_remaining=tokens_remaining,
            fraction_used=fraction_used,
            tokens_per_hour=tokens_per_hour,
            minutes_to_limit=minutes_to_limit,
            warning_level=level,
            sample_count=sample_count,
        )

    def current_estimate(self, window_start: datetime) -> WindowEstimate:
        """
        Query usage_store for the current window and return a live estimate.
        Requires usage_store to be set at construction time.
        """
        if self.usage_store is None:
            raise ValueError("usage_store required for current_estimate()")

        # Tokens consumed since window_start
        rows = self.usage_store.conn.execute(
            """
            SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS total,
                   (julianday(MAX(timestamp)) - julianday(MIN(timestamp))) * 24.0 AS hours_span
            FROM token_events
            WHERE timestamp >= ?
            """,
            (window_start.isoformat(),),
        ).fetchone()

        tokens_used = rows["total"] or 0
        hours_span = rows["hours_span"] or 0.0
        tokens_per_hour = (tokens_used / hours_span) if hours_span > 0 else 0.0

        return self.estimate(tokens_used, tokens_per_hour)


def _compute_estimate(
    task_type: str,
    model: str,
    samples: list[int],
    existing: CostEstimate | None,
) -> CostEstimate:
    if not samples:
        p50 = DEFAULT_TOKEN_ESTIMATES.get(task_type, 20_000)
        p90 = int(p50 * DEFAULT_P90_MULTIPLIER)
        return CostEstimate(
            task_type=task_type,
            model=model,
            p50_tokens=p50,
            p90_tokens=p90,
            sample_count=0,
            last_updated=datetime.now(timezone.utc),
        )

    sorted_samples = sorted(samples)
    n = len(sorted_samples)
    p50 = _percentile(sorted_samples, 50)
    p90 = _percentile(sorted_samples, 90)

    return CostEstimate(
        task_type=task_type,
        model=model,
        p50_tokens=p50,
        p90_tokens=p90,
        sample_count=n,
        last_updated=datetime.now(timezone.utc),
    )


def _percentile(sorted_values: list[int], pct: int) -> int:
    if not sorted_values:
        return 0
    n = len(sorted_values)
    idx = (pct / 100) * (n - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_values[lo]
    frac = idx - lo
    return int(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac)
