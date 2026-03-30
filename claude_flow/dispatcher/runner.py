"""TaskRunner: translates a Task + AgentConfig into a claude CLI invocation."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from claude_flow.graph.models import Task
from claude_flow.scheduler.models import AgentConfig, RunStatus, TaskRun
from claude_flow.scheduler.store import SchedulerStore
from claude_flow.dispatcher.process import RunningProcess
from claude_flow.dispatcher.watcher import OutputWatcher

DEFAULT_OUTPUT_DIR = Path.home() / ".claudeflow" / "runs"


class TaskRunError(Exception):
    """Raised when a task exits with a non-zero exit code."""

    def __init__(self, message: str, run: TaskRun) -> None:
        super().__init__(message)
        self.run = run


class TaskRunner:
    def __init__(
        self,
        store: SchedulerStore,
        output_dir: Path | None = None,
    ) -> None:
        self.store = store
        self.output_dir = output_dir or DEFAULT_OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def run(
        self,
        task: Task,
        config: AgentConfig,
        window_id: str,
        dry_run: bool = False,
    ) -> TaskRun:
        """
        Execute the task via `claude` CLI. Returns a completed TaskRun.
        Raises TaskRunError on non-zero exit code.
        """
        run_id = str(uuid.uuid4())
        args = self.build_cli_args(task, config)
        prompt = self.render_prompt(task, config)

        if dry_run:
            return _make_dry_run(run_id, task.id, window_id, config.model, args, prompt)

        _check_claude_available()

        stdout_path = self.output_dir / f"{run_id}.stdout"
        stderr_path = self.output_dir / f"{run_id}.stderr"
        started_at = datetime.now(timezone.utc)

        run = TaskRun(
            run_id=run_id,
            task_id=task.id,
            window_id=window_id,
            model=config.model,
            started_at=started_at,
            status=RunStatus.RUNNING,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )
        self.store.save_run(run)

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=config.working_directory,
            env={**_base_env(), **config.env_vars},
        )

        running = RunningProcess(
            run_id=run_id,
            task_id=task.id,
            proc=proc,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            started_at=started_at,
        )

        watcher = OutputWatcher(proc.stdout, run_id, model=config.model)
        watch_result = await watcher.consume()

        # Drain stderr
        stderr_bytes = await proc.stderr.read()
        stderr_path.write_bytes(stderr_bytes)
        stdout_path.write_text(watch_result.full_text, encoding="utf-8")

        exit_code = await running.wait()
        completed_at = datetime.now(timezone.utc)

        status = RunStatus.COMPLETED if exit_code == 0 else RunStatus.FAILED
        run.exit_code = exit_code
        run.completed_at = completed_at
        run.status = status
        run.actual_input_tokens = watch_result.usage.input_tokens
        run.actual_output_tokens = watch_result.usage.output_tokens
        run.actual_cost_usd = watch_result.usage.cost_usd

        self.store.save_run(run)

        if exit_code != 0:
            err = watch_result.error_message or stderr_bytes.decode("utf-8", errors="replace")[:500]
            raise TaskRunError(f"Task {task.id} failed (exit {exit_code}): {err}", run)

        return run

    def build_cli_args(self, task: Task, config: AgentConfig) -> list[str]:
        """Build the full `claude` CLI argument list."""
        prompt = self.render_prompt(task, config)
        args = ["claude", "--print", "--output-format", "stream-json"]
        args += ["--model", config.model]
        if config.max_turns is not None:
            args += ["--max-turns", str(config.max_turns)]
        if config.allowed_tools:
            args += ["--allowedTools", ",".join(config.allowed_tools)]
        if config.disallowed_tools:
            args += ["--disallowedTools", ",".join(config.disallowed_tools)]
        args += config.extra_flags
        args.append(prompt)
        return args

    def render_prompt(self, task: Task, config: AgentConfig) -> str:
        """Render the prompt template with task context using Jinja2."""
        try:
            from jinja2 import Template
            return Template(config.prompt_template).render(task=task)
        except ImportError:
            # Fallback: simple string replacement
            return (
                config.prompt_template
                .replace("{{ task.name }}", task.name)
                .replace("{{ task.id }}", task.id)
                .replace("{{ task.task_type.value }}", task.task_type.value)
                .replace("{{ task.estimated_hours }}", str(task.estimated_hours))
            )


def _check_claude_available() -> None:
    if not shutil.which("claude"):
        raise RuntimeError(
            "claude CLI not found. Install it with: npm install -g @anthropic-ai/claude-code"
        )


def _base_env() -> dict[str, str]:
    import os
    return dict(os.environ)


def _make_dry_run(
    run_id: str,
    task_id: str,
    window_id: str,
    model: str,
    args: list[str],
    prompt: str,
) -> TaskRun:
    now = datetime.now(timezone.utc)
    return TaskRun(
        run_id=run_id,
        task_id=task_id,
        window_id=window_id,
        model=model,
        started_at=now,
        completed_at=now,
        exit_code=0,
        status=RunStatus.COMPLETED,
        actual_input_tokens=0,
        actual_output_tokens=0,
        actual_cost_usd=0.0,
    )
