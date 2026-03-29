"""Tests for the observer module."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from claude_flow.observer.parser import (
    SessionRecord,
    TokenEvent,
    UsageParser,
    estimate_cost,
    _parse_timestamp,
)
from claude_flow.observer.store import UsageStore
from claude_flow.observer.report import UsageReport


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_EVENTS = [
    {
        "session_id": "sess-001",
        "timestamp": "2026-03-15T10:00:00Z",
        "model": "claude-sonnet-4-6",
        "input_tokens": 1500,
        "output_tokens": 500,
    },
    {
        "session_id": "sess-001",
        "timestamp": "2026-03-15T10:05:00Z",
        "model": "claude-sonnet-4-6",
        "input_tokens": 2000,
        "output_tokens": 800,
    },
    {
        "session_id": "sess-002",
        "timestamp": "2026-03-15T14:00:00Z",
        "model": "claude-opus-4-6",
        "input_tokens": 5000,
        "output_tokens": 3000,
    },
]

SAMPLE_EVENTS_CAMEL = [
    {
        "sessionId": "sess-003",
        "ts": 1710500400000,  # millisecond timestamp
        "modelId": "claude-haiku-4-5-20251001",
        "inputTokens": 800,
        "outputTokens": 200,
    },
]

# Real Claude Code log format: tokens nested under message.usage
SAMPLE_EVENTS_REAL_FORMAT = [
    {
        "type": "assistant",
        "sessionId": "sess-004",
        "timestamp": "2026-03-15T10:00:00.000Z",
        "message": {
            "model": "claude-opus-4-6",
            "role": "assistant",
            "usage": {
                "input_tokens": 3,
                "cache_creation_input_tokens": 14678,
                "cache_read_input_tokens": 0,
                "output_tokens": 20,
            },
        },
    },
    {
        "type": "assistant",
        "sessionId": "sess-004",
        "timestamp": "2026-03-15T10:01:00.000Z",
        "message": {
            "model": "claude-opus-4-6",
            "role": "assistant",
            "usage": {
                "input_tokens": 500,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 10000,
                "output_tokens": 2500,
            },
        },
    },
    {
        "type": "user",
        "sessionId": "sess-004",
        "timestamp": "2026-03-15T10:00:30.000Z",
        "message": {"role": "user", "content": "hello"},
    },
]


@pytest.fixture
def jsonl_dir(tmp_path: Path) -> Path:
    """Create a fake .claude/projects directory with JSONL files."""
    project_dir = tmp_path / ".claude" / "projects" / "my-project"
    project_dir.mkdir(parents=True)

    log_file = project_dir / "usage.jsonl"
    lines = [json.dumps(ev) for ev in SAMPLE_EVENTS]
    log_file.write_text("\n".join(lines) + "\n")

    project2_dir = tmp_path / ".claude" / "projects" / "other-project"
    project2_dir.mkdir(parents=True)
    log2 = project2_dir / "events.jsonl"
    lines2 = [json.dumps(ev) for ev in SAMPLE_EVENTS_CAMEL]
    log2.write_text("\n".join(lines2) + "\n")

    project3_dir = tmp_path / ".claude" / "projects" / "real-project"
    project3_dir.mkdir(parents=True)
    log3 = project3_dir / "session.jsonl"
    lines3 = [json.dumps(ev) for ev in SAMPLE_EVENTS_REAL_FORMAT]
    log3.write_text("\n".join(lines3) + "\n")

    return tmp_path / ".claude"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_usage.db"


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestTimestampParsing:
    def test_iso_format(self):
        dt = _parse_timestamp("2026-03-15T10:00:00Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.hour == 10

    def test_unix_seconds(self):
        dt = _parse_timestamp(1710500400)
        assert dt is not None
        assert isinstance(dt, datetime)

    def test_unix_milliseconds(self):
        dt = _parse_timestamp(1710500400000)
        assert dt is not None
        assert isinstance(dt, datetime)

    def test_none_input(self):
        assert _parse_timestamp(None) is None

    def test_garbage_string(self):
        assert _parse_timestamp("not-a-date") is None


class TestEstimateCost:
    def test_known_model(self):
        cost = estimate_cost("claude-sonnet-4-6", 1000, 1000)
        # input: 1000 * 3/1M = 0.003, output: 1000 * 15/1M = 0.015
        assert abs(cost - 0.018) < 0.001

    def test_unknown_model_uses_defaults(self):
        cost = estimate_cost("unknown-model", 1000, 1000)
        assert cost > 0

    def test_zero_tokens(self):
        assert estimate_cost("claude-sonnet-4-6", 0, 0) == 0.0


class TestUsageParser:
    def test_find_jsonl_files(self, jsonl_dir: Path):
        parser = UsageParser(claude_dir=jsonl_dir)
        files = parser.find_jsonl_files()
        assert len(files) == 3

    def test_parse_all(self, jsonl_dir: Path):
        parser = UsageParser(claude_dir=jsonl_dir)
        records = parser.parse_all()
        assert len(records) == 4  # sess-001, sess-002, sess-003, sess-004

    def test_session_fields(self, jsonl_dir: Path):
        parser = UsageParser(claude_dir=jsonl_dir)
        records = parser.parse_all()
        by_id = {r.session_id: r for r in records}

        sess1 = by_id["sess-001"]
        assert sess1.model == "claude-sonnet-4-6"
        assert sess1.project == "my-project"
        assert sess1.duration_ms > 0
        assert len(sess1.token_events) == 2

    def test_camel_case_fields(self, jsonl_dir: Path):
        parser = UsageParser(claude_dir=jsonl_dir)
        records = parser.parse_all()
        by_id = {r.session_id: r for r in records}

        sess3 = by_id["sess-003"]
        assert sess3.model == "claude-haiku-4-5-20251001"
        assert len(sess3.token_events) == 1
        assert sess3.token_events[0].input_tokens == 800

    def test_real_log_format_nested_usage(self, jsonl_dir: Path):
        """Test parsing real Claude Code format with message.usage nesting."""
        parser = UsageParser(claude_dir=jsonl_dir)
        records = parser.parse_all()
        by_id = {r.session_id: r for r in records}

        sess4 = by_id["sess-004"]
        assert sess4.model == "claude-opus-4-6"
        assert sess4.project == "real-project"
        # Should have 2 token events (the user message has no usage)
        assert len(sess4.token_events) == 2

        # First event: 3 input + 14678 cache_creation + 0 cache_read = 14681
        ev0 = sess4.token_events[0]
        assert ev0.input_tokens == 14681
        assert ev0.output_tokens == 20

        # Second event: 500 input + 0 cache_creation + 10000 cache_read = 10500
        ev1 = sess4.token_events[1]
        assert ev1.input_tokens == 10500
        assert ev1.output_tokens == 2500
        assert ev1.cost_usd > 0

    def test_empty_dir(self, tmp_path: Path):
        parser = UsageParser(claude_dir=tmp_path / "nonexistent")
        assert parser.find_jsonl_files() == []
        assert parser.parse_all() == []

    def test_malformed_json_lines(self, tmp_path: Path):
        project_dir = tmp_path / ".claude" / "projects" / "test"
        project_dir.mkdir(parents=True)
        log = project_dir / "bad.jsonl"
        log.write_text("not json\n{\"session_id\": \"s1\", \"input_tokens\": 10}\n")

        parser = UsageParser(claude_dir=tmp_path / ".claude")
        records = parser.parse_all()
        assert len(records) == 1


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------


class TestUsageStore:
    def test_ingest_and_query(self, jsonl_dir: Path, db_path: Path):
        parser = UsageParser(claude_dir=jsonl_dir)
        records = parser.parse_all()

        store = UsageStore(db_path=db_path)
        count = store.ingest(records)
        assert count == 4

        assert store.session_count() == 4

        input_tok, output_tok = store.total_tokens()
        assert input_tok > 0
        assert output_tok > 0
        store.close()

    def test_peak_hours(self, jsonl_dir: Path, db_path: Path):
        parser = UsageParser(claude_dir=jsonl_dir)
        store = UsageStore(db_path=db_path)
        store.ingest(parser.parse_all())

        hours = store.peak_usage_hours()
        assert len(hours) > 0
        assert all(0 <= h <= 23 for h, _ in hours)
        store.close()

    def test_top_projects(self, jsonl_dir: Path, db_path: Path):
        parser = UsageParser(claude_dir=jsonl_dir)
        store = UsageStore(db_path=db_path)
        store.ingest(parser.parse_all())

        projects = store.top_projects(3)
        assert len(projects) > 0
        # First project should have most tokens
        assert projects[0][1] >= projects[-1][1]
        store.close()

    def test_clear(self, jsonl_dir: Path, db_path: Path):
        parser = UsageParser(claude_dir=jsonl_dir)
        store = UsageStore(db_path=db_path)
        store.ingest(parser.parse_all())
        assert store.session_count() > 0

        store.clear()
        assert store.session_count() == 0
        store.close()

    def test_burn_rate_empty(self, db_path: Path):
        store = UsageStore(db_path=db_path)
        tokens_hr, cost_hr = store.burn_rate()
        assert tokens_hr == 0.0
        assert cost_hr == 0.0
        store.close()


# ---------------------------------------------------------------------------
# Report tests (smoke test - just ensure no exceptions)
# ---------------------------------------------------------------------------


class TestUsageReport:
    def test_full_report_runs(self, jsonl_dir: Path, db_path: Path):
        from rich.console import Console
        from io import StringIO

        parser = UsageParser(claude_dir=jsonl_dir)
        store = UsageStore(db_path=db_path)
        store.ingest(parser.parse_all())

        output = StringIO()
        report = UsageReport(store, console=Console(file=output, force_terminal=True))
        report.print_full_report()

        text = output.getvalue()
        assert "Summary" in text
        assert "Burn Rate" in text
        store.close()

    def test_empty_report(self, db_path: Path):
        from rich.console import Console
        from io import StringIO

        store = UsageStore(db_path=db_path)
        output = StringIO()
        report = UsageReport(store, console=Console(file=output, force_terminal=True))
        report.print_full_report()  # should not raise
        store.close()
