"""Tests for the dispatcher module."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_flow.dispatcher.watcher import OutputWatcher, TokenUsage, WatcherResult
from claude_flow.dispatcher.runner import TaskRunner, TaskRunError, _check_claude_available
from claude_flow.dispatcher.process import RunningProcess
from claude_flow.graph.models import Task, TaskType, TaskStatus
from claude_flow.scheduler.models import AgentConfig, RunStatus
from claude_flow.scheduler.store import SchedulerStore


# ---------------------------------------------------------------------------
# TokenUsage
# ---------------------------------------------------------------------------


class TestTokenUsage:
    def test_total_tokens(self):
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        assert usage.total_tokens == 150

    def test_zero_tokens(self):
        usage = TokenUsage()
        assert usage.total_tokens == 0

    def test_cost_usd_uses_model_rate(self):
        usage = TokenUsage(input_tokens=1_000_000, output_tokens=0)
        usage._model = "claude-sonnet-4-6"
        cost = usage.cost_usd
        assert cost > 0
        assert isinstance(cost, float)

    def test_cost_usd_default_model(self):
        usage = TokenUsage(input_tokens=1000, output_tokens=500)
        cost = usage.cost_usd
        assert cost >= 0


# ---------------------------------------------------------------------------
# WatcherResult
# ---------------------------------------------------------------------------


class TestWatcherResult:
    def test_full_text_empty(self):
        result = WatcherResult()
        assert result.full_text == ""

    def test_full_text_concatenates_chunks(self):
        result = WatcherResult(content_chunks=["hello ", "world"])
        assert result.full_text == "hello world"

    def test_default_fields(self):
        result = WatcherResult()
        assert result.content_chunks == []
        assert result.error_message is None
        assert result.raw_lines == []
        assert result.usage.total_tokens == 0


# ---------------------------------------------------------------------------
# OutputWatcher
# ---------------------------------------------------------------------------


def _make_stream(lines: list[str]) -> asyncio.StreamReader:
    """Create a StreamReader pre-loaded with newline-delimited data."""
    reader = asyncio.StreamReader()
    for line in lines:
        reader.feed_data((line + "\n").encode())
    reader.feed_eof()
    return reader


class TestOutputWatcher:
    @pytest.mark.asyncio
    async def test_empty_stream(self):
        stream = _make_stream([])
        watcher = OutputWatcher(stream, run_id="r1")
        result = await watcher.consume()
        assert result.full_text == ""
        assert result.usage.total_tokens == 0

    @pytest.mark.asyncio
    async def test_text_event(self):
        line = json.dumps({"type": "text", "text": "Hello from Claude"})
        stream = _make_stream([line])
        watcher = OutputWatcher(stream, run_id="r1")
        result = await watcher.consume()
        assert "Hello from Claude" in result.full_text

    @pytest.mark.asyncio
    async def test_assistant_event_extracts_text(self):
        line = json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "I'll help with that."},
                    {"type": "tool_use", "name": "bash"},
                ]
            }
        })
        stream = _make_stream([line])
        watcher = OutputWatcher(stream, run_id="r1")
        result = await watcher.consume()
        assert result.full_text == "I'll help with that."

    @pytest.mark.asyncio
    async def test_result_event_captures_usage(self):
        line = json.dumps({
            "type": "result",
            "subtype": "success",
            "usage": {"input_tokens": 500, "output_tokens": 200}
        })
        stream = _make_stream([line])
        watcher = OutputWatcher(stream, run_id="r1")
        result = await watcher.consume()
        assert result.usage.input_tokens == 500
        assert result.usage.output_tokens == 200
        assert result.error_message is None

    @pytest.mark.asyncio
    async def test_result_error_event(self):
        line = json.dumps({
            "type": "result",
            "subtype": "error",
            "error": {"message": "Rate limited"},
            "usage": {"input_tokens": 100, "output_tokens": 0}
        })
        stream = _make_stream([line])
        watcher = OutputWatcher(stream, run_id="r1")
        result = await watcher.consume()
        assert result.error_message == "Rate limited"
        assert result.usage.input_tokens == 100

    @pytest.mark.asyncio
    async def test_error_event(self):
        line = json.dumps({
            "type": "error",
            "error": {"message": "Something broke"}
        })
        stream = _make_stream([line])
        watcher = OutputWatcher(stream, run_id="r1")
        result = await watcher.consume()
        assert result.error_message == "Something broke"

    @pytest.mark.asyncio
    async def test_malformed_json_treated_as_text(self):
        stream = _make_stream(["not valid json"])
        watcher = OutputWatcher(stream, run_id="r1")
        result = await watcher.consume()
        assert "not valid json" in result.full_text

    @pytest.mark.asyncio
    async def test_blank_lines_skipped(self):
        stream = _make_stream(["", "  ", json.dumps({"type": "text", "text": "ok"})])
        watcher = OutputWatcher(stream, run_id="r1")
        result = await watcher.consume()
        assert result.full_text == "ok"

    @pytest.mark.asyncio
    async def test_mixed_events(self):
        lines = [
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Step 1. "}]}}),
            json.dumps({"type": "text", "text": "Step 2. "}),
            json.dumps({"type": "result", "subtype": "success", "usage": {"input_tokens": 1000, "output_tokens": 300}}),
        ]
        stream = _make_stream(lines)
        watcher = OutputWatcher(stream, run_id="r1")
        result = await watcher.consume()
        assert "Step 1." in result.full_text
        assert "Step 2." in result.full_text
        assert result.usage.input_tokens == 1000
        assert result.usage.output_tokens == 300

    @pytest.mark.asyncio
    async def test_cache_tokens_parsed(self):
        line = json.dumps({
            "type": "result",
            "subtype": "success",
            "usage": {
                "input_tokens": 500,
                "output_tokens": 100,
                "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 50,
            }
        })
        stream = _make_stream([line])
        watcher = OutputWatcher(stream, run_id="r1")
        result = await watcher.consume()
        assert result.usage.cache_creation_tokens == 200
        assert result.usage.cache_read_tokens == 50


# ---------------------------------------------------------------------------
# RunningProcess
# ---------------------------------------------------------------------------


class TestRunningProcess:
    def test_is_running_true(self):
        proc = MagicMock()
        proc.returncode = None
        rp = RunningProcess(
            run_id="r1", task_id="T1", proc=proc,
            stdout_path=Path("/tmp/out"), stderr_path=Path("/tmp/err"),
            started_at=datetime.now(timezone.utc),
        )
        assert rp.is_running is True

    def test_is_running_false_after_exit(self):
        proc = MagicMock()
        proc.returncode = 0
        rp = RunningProcess(
            run_id="r1", task_id="T1", proc=proc,
            stdout_path=Path("/tmp/out"), stderr_path=Path("/tmp/err"),
            started_at=datetime.now(timezone.utc),
        )
        assert rp.is_running is False

    @pytest.mark.asyncio
    async def test_cancel_already_exited(self):
        proc = AsyncMock()
        proc.returncode = 0
        rp = RunningProcess(
            run_id="r1", task_id="T1", proc=proc,
            stdout_path=Path("/tmp/out"), stderr_path=Path("/tmp/err"),
            started_at=datetime.now(timezone.utc),
        )
        await rp.cancel()
        proc.terminate.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_terminates(self):
        proc = AsyncMock()
        proc.returncode = None
        proc.wait = AsyncMock(return_value=0)
        # After terminate, pretend it exited
        rp = RunningProcess(
            run_id="r1", task_id="T1", proc=proc,
            stdout_path=Path("/tmp/out"), stderr_path=Path("/tmp/err"),
            started_at=datetime.now(timezone.utc),
        )
        await rp.cancel()
        proc.terminate.assert_called_once()


# ---------------------------------------------------------------------------
# TaskRunner
# ---------------------------------------------------------------------------


class TestTaskRunner:
    @pytest.fixture
    def store(self, tmp_path):
        return SchedulerStore(tmp_path / "test.db")

    @pytest.fixture
    def task(self):
        return Task("T1", "Fix login bug", TaskType.BUG_FIX, 2.0, TaskStatus.PENDING)

    @pytest.fixture
    def config(self, tmp_path):
        return AgentConfig(
            config_id="cfg1",
            prompt_template="Complete: {{ task.name }}",
            working_directory=str(tmp_path),
            model="claude-sonnet-4-6",
        )

    def test_build_cli_args_basic(self, store, task, config, tmp_path):
        runner = TaskRunner(store=store, output_dir=tmp_path / "runs")
        args = runner.build_cli_args(task, config)
        assert args[0] == "claude"
        assert "--print" in args
        assert "--output-format" in args
        assert "stream-json" in args
        assert "--model" in args
        assert "claude-sonnet-4-6" in args

    def test_build_cli_args_with_max_turns(self, store, task, tmp_path):
        config = AgentConfig(
            config_id="cfg1",
            prompt_template="Do: {{ task.name }}",
            working_directory=str(tmp_path),
            max_turns=5,
        )
        runner = TaskRunner(store=store, output_dir=tmp_path / "runs")
        args = runner.build_cli_args(task, config)
        idx = args.index("--max-turns")
        assert args[idx + 1] == "5"

    def test_build_cli_args_with_allowed_tools(self, store, task, tmp_path):
        config = AgentConfig(
            config_id="cfg1",
            prompt_template="Do: {{ task.name }}",
            working_directory=str(tmp_path),
            allowed_tools=["Read", "Write"],
        )
        runner = TaskRunner(store=store, output_dir=tmp_path / "runs")
        args = runner.build_cli_args(task, config)
        idx = args.index("--allowedTools")
        assert args[idx + 1] == "Read,Write"

    def test_build_cli_args_with_disallowed_tools(self, store, task, tmp_path):
        config = AgentConfig(
            config_id="cfg1",
            prompt_template="Do: {{ task.name }}",
            working_directory=str(tmp_path),
            disallowed_tools=["Bash"],
        )
        runner = TaskRunner(store=store, output_dir=tmp_path / "runs")
        args = runner.build_cli_args(task, config)
        idx = args.index("--disallowedTools")
        assert args[idx + 1] == "Bash"

    def test_render_prompt_jinja(self, store, task, config, tmp_path):
        runner = TaskRunner(store=store, output_dir=tmp_path / "runs")
        prompt = runner.render_prompt(task, config)
        assert "Fix login bug" in prompt

    @pytest.mark.asyncio
    async def test_dry_run(self, store, task, config, tmp_path):
        runner = TaskRunner(store=store, output_dir=tmp_path / "runs")
        result = await runner.run(task, config, window_id="w1", dry_run=True)
        assert result.status == RunStatus.COMPLETED
        assert result.task_id == "T1"
        assert result.actual_input_tokens == 0
        assert result.exit_code == 0

    def test_check_claude_available_missing(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="claude CLI not found"):
                _check_claude_available()

    def test_check_claude_available_found(self):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            _check_claude_available()  # Should not raise

    def test_output_dir_created(self, store, tmp_path):
        output_dir = tmp_path / "new" / "runs"
        assert not output_dir.exists()
        TaskRunner(store=store, output_dir=output_dir)
        assert output_dir.exists()
