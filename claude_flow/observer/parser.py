"""Recursively scan and parse Claude Code JSONL usage logs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class TokenEvent:
    session_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    timestamp: datetime


@dataclass
class SessionRecord:
    session_id: str
    project: str
    model: str
    started_at: datetime
    ended_at: datetime
    duration_ms: int
    token_events: list[TokenEvent] = field(default_factory=list)


# Approximate cost per token by model (USD)
COST_PER_INPUT_TOKEN: dict[str, float] = {
    "claude-opus-4-6": 15.0 / 1_000_000,
    "claude-sonnet-4-6": 3.0 / 1_000_000,
    "claude-haiku-4-5-20251001": 0.80 / 1_000_000,
}

DEFAULT_COST_PER_INPUT = 3.0 / 1_000_000
COST_PER_OUTPUT_TOKEN: dict[str, float] = {
    "claude-opus-4-6": 75.0 / 1_000_000,
    "claude-sonnet-4-6": 15.0 / 1_000_000,
    "claude-haiku-4-5-20251001": 4.0 / 1_000_000,
}
DEFAULT_COST_PER_OUTPUT = 15.0 / 1_000_000


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    input_cost = COST_PER_INPUT_TOKEN.get(model, DEFAULT_COST_PER_INPUT) * input_tokens
    output_cost = COST_PER_OUTPUT_TOKEN.get(model, DEFAULT_COST_PER_OUTPUT) * output_tokens
    return round(input_cost + output_cost, 6)


class UsageParser:
    """Scan ~/.claude/projects/**/*.jsonl and extract session + token data."""

    def __init__(self, claude_dir: Path | None = None) -> None:
        self.claude_dir = claude_dir or Path.home() / ".claude"

    def find_jsonl_files(self) -> list[Path]:
        projects_dir = self.claude_dir / "projects"
        if not projects_dir.exists():
            return []
        return sorted(projects_dir.rglob("*.jsonl"))

    def parse_file(self, path: Path) -> list[SessionRecord]:
        """Parse a single JSONL file into session records."""
        # Derive project from directory structure:
        # ~/.claude/projects/<project_path>/<file>.jsonl
        project = _extract_project(path, self.claude_dir)
        events_by_session: dict[str, list[dict]] = {}

        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = entry.get("session_id") or entry.get("sessionId", "unknown")
                events_by_session.setdefault(sid, []).append(entry)

        return [
            self._build_session(sid, events, project)
            for sid, events in events_by_session.items()
        ]

    def parse_all(self) -> list[SessionRecord]:
        records: list[SessionRecord] = []
        for path in self.find_jsonl_files():
            records.extend(self.parse_file(path))
        return records

    def _build_session(
        self, session_id: str, events: list[dict], project: str
    ) -> SessionRecord:
        timestamps: list[datetime] = []
        model = "unknown"
        token_events: list[TokenEvent] = []

        for ev in events:
            ts = _parse_timestamp(ev.get("timestamp") or ev.get("ts"))
            if ts:
                timestamps.append(ts)

            # Model can be at top level or nested in message
            msg = ev.get("message") or {}
            ev_model = (
                ev.get("model")
                or ev.get("modelId")
                or msg.get("model")
                or msg.get("modelId")
            )
            if ev_model:
                model = ev_model

            # Token usage can be at top level or nested in message.usage
            usage = msg.get("usage") or {}
            input_tok = (
                ev.get("input_tokens")
                or ev.get("inputTokens")
                or usage.get("input_tokens", 0)
            )
            output_tok = (
                ev.get("output_tokens")
                or ev.get("outputTokens")
                or usage.get("output_tokens", 0)
            )
            # Include cached tokens in the total input count
            cache_creation = usage.get("cache_creation_input_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            input_tok += cache_creation + cache_read

            if input_tok or output_tok:
                cost = estimate_cost(model, input_tok, output_tok)
                token_events.append(
                    TokenEvent(
                        session_id=session_id,
                        input_tokens=input_tok,
                        output_tokens=output_tok,
                        cost_usd=cost,
                        timestamp=ts or datetime.now(timezone.utc),
                    )
                )

        if timestamps:
            started_at = min(timestamps)
            ended_at = max(timestamps)
            duration_ms = int((ended_at - started_at).total_seconds() * 1000)
        else:
            started_at = ended_at = datetime.now(timezone.utc)
            duration_ms = 0

        return SessionRecord(
            session_id=session_id,
            project=project,
            model=model,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            token_events=token_events,
        )


def _extract_project(path: Path, claude_dir: Path) -> str:
    try:
        rel = path.relative_to(claude_dir / "projects")
        # Project is everything except the filename
        return str(rel.parent) if rel.parent != Path(".") else str(rel.stem)
    except ValueError:
        return "unknown"


def _parse_timestamp(value: str | int | float | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Unix timestamp (seconds or milliseconds)
        if value > 1e12:
            value = value / 1000
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                dt = datetime.strptime(value, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
    return None
