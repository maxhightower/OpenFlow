"""SQLite storage for parsed usage data."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from claude_flow.observer.parser import SessionRecord


DEFAULT_DB_PATH = Path(__file__).parent / "usage.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    project TEXT NOT NULL,
    model TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS token_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_token_events_session_id ON token_events(session_id);
"""


class UsageStore:
    """Persist and query usage data in SQLite."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(SCHEMA)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def clear(self) -> None:
        self.conn.executescript("DELETE FROM token_events; DELETE FROM sessions;")

    def ingest(self, records: list[SessionRecord]) -> int:
        """Insert parsed session records. Returns count of sessions inserted."""
        cur = self.conn.cursor()
        count = 0
        for rec in records:
            cur.execute(
                """INSERT INTO sessions (session_id, project, model, started_at, ended_at, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    rec.session_id,
                    rec.project,
                    rec.model,
                    rec.started_at.isoformat(),
                    rec.ended_at.isoformat(),
                    rec.duration_ms,
                ),
            )
            count += 1
            for te in rec.token_events:
                cur.execute(
                    """INSERT INTO token_events (session_id, input_tokens, output_tokens, cost_usd, timestamp)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        te.session_id,
                        te.input_tokens,
                        te.output_tokens,
                        te.cost_usd,
                        te.timestamp.isoformat(),
                    ),
                )
        self.conn.commit()
        return count

    def peak_usage_hours(self) -> list[tuple[int, int]]:
        """Return (hour, event_count) sorted by hour."""
        rows = self.conn.execute(
            """SELECT CAST(strftime('%H', timestamp) AS INTEGER) AS hour,
                      COUNT(*) AS cnt
               FROM token_events
               GROUP BY hour ORDER BY hour"""
        ).fetchall()
        return [(r["hour"], r["cnt"]) for r in rows]

    def avg_tokens_by_model(self) -> list[tuple[str, float, float]]:
        """Return (model, avg_input, avg_output) per model."""
        rows = self.conn.execute(
            """SELECT s.model,
                      AVG(te.input_tokens) AS avg_in,
                      AVG(te.output_tokens) AS avg_out
               FROM token_events te
               JOIN sessions s ON s.session_id = te.session_id
               GROUP BY s.model"""
        ).fetchall()
        return [(r["model"], r["avg_in"], r["avg_out"]) for r in rows]

    def burn_rate(self) -> tuple[float, float]:
        """Return (tokens_per_hour, cost_per_hour) across all data."""
        row = self.conn.execute(
            """SELECT
                 SUM(input_tokens + output_tokens) AS total_tokens,
                 SUM(cost_usd) AS total_cost,
                 (julianday(MAX(timestamp)) - julianday(MIN(timestamp))) * 24 AS hours_span
               FROM token_events"""
        ).fetchone()
        if not row or not row["hours_span"] or row["hours_span"] <= 0:
            return (0.0, 0.0)
        return (
            row["total_tokens"] / row["hours_span"],
            row["total_cost"] / row["hours_span"],
        )

    def total_cost(self) -> float:
        row = self.conn.execute("SELECT SUM(cost_usd) AS total FROM token_events").fetchone()
        return row["total"] or 0.0

    def top_projects(self, n: int = 3) -> list[tuple[str, int]]:
        """Return top N projects by total tokens."""
        rows = self.conn.execute(
            """SELECT s.project,
                      SUM(te.input_tokens + te.output_tokens) AS total_tokens
               FROM token_events te
               JOIN sessions s ON s.session_id = te.session_id
               GROUP BY s.project
               ORDER BY total_tokens DESC
               LIMIT ?""",
            (n,),
        ).fetchall()
        return [(r["project"], r["total_tokens"]) for r in rows]

    def session_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS cnt FROM sessions").fetchone()
        return row["cnt"]

    def total_tokens(self) -> tuple[int, int]:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(input_tokens),0) AS i, COALESCE(SUM(output_tokens),0) AS o FROM token_events"
        ).fetchone()
        return (row["i"], row["o"])
