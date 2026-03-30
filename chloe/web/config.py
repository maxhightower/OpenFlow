"""Web application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8420
    db_path: Path = field(default_factory=lambda: Path.home() / ".chloe" / "chloe.db")
    token_budget: int = 500_000
    model: str = "claude-sonnet-4-6"
    api_key: str | None = None  # Optional bearer token auth
    cors_origins: list[str] = field(default_factory=lambda: ["*"])

    @classmethod
    def from_env(cls) -> WebConfig:
        return cls(
            host=os.environ.get("CLAUDEFLOW_HOST", "127.0.0.1"),
            port=int(os.environ.get("CLAUDEFLOW_PORT", "8420")),
            db_path=Path(os.environ.get(
                "CLAUDEFLOW_DB", str(Path.home() / ".chloe" / "chloe.db")
            )),
            token_budget=int(os.environ.get("CLAUDEFLOW_TOKEN_BUDGET", "500000")),
            model=os.environ.get("CLAUDEFLOW_MODEL", "claude-sonnet-4-6"),
            api_key=os.environ.get("CLAUDEFLOW_API_KEY"),
        )
