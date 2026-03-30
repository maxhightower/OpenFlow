"""RunningProcess: wraps an async subprocess for a dispatched task."""

from __future__ import annotations

import asyncio
import signal
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class RunningProcess:
    run_id: str
    task_id: str
    proc: asyncio.subprocess.Process
    stdout_path: Path
    stderr_path: Path
    started_at: datetime

    async def wait(self) -> int:
        """Await completion and return exit code."""
        return await self.proc.wait()

    async def cancel(self) -> None:
        """Gracefully terminate, then force-kill if needed."""
        if self.proc.returncode is not None:
            return
        try:
            self.proc.terminate()
            await asyncio.wait_for(self.proc.wait(), timeout=10.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                self.proc.kill()
                await self.proc.wait()
            except ProcessLookupError:
                pass

    @property
    def is_running(self) -> bool:
        return self.proc.returncode is None
