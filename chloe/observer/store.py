"""SQLite storage for parsed usage data."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from chloe.observer.parser import SessionRecord


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

    def usage_by_hour(self) -> list[dict]:
        """Return event counts, tokens, and cost for each of the 24 hours."""
        rows = self.conn.execute(
            """SELECT CAST(strftime('%H', timestamp) AS INTEGER) AS hour,
                      COUNT(*) AS cnt,
                      SUM(cost_usd) AS cost,
                      SUM(input_tokens + output_tokens) AS tokens
               FROM token_events
               GROUP BY hour"""
        ).fetchall()
        by_hour = {i: {"cnt": 0, "cost": 0.0, "tokens": 0} for i in range(24)}
        for r in rows:
            by_hour[r["hour"]].update({"cnt": r["cnt"], "cost": r["cost"] or 0.0, "tokens": r["tokens"] or 0})
        return [{"hour": h, **v} for h, v in by_hour.items()]

    def usage_by_shift(self) -> list[dict]:
        """Return event counts and cost binned into 4-hour shifts."""
        rows = self.conn.execute(
            """SELECT CAST(strftime('%H', timestamp) AS INTEGER) AS hour,
                      COUNT(*) AS cnt,
                      SUM(cost_usd) AS cost,
                      SUM(input_tokens + output_tokens) AS tokens
               FROM token_events
               GROUP BY hour"""
        ).fetchall()
        shifts = {
            "12 AM – 4 AM": {"cnt": 0, "cost": 0.0, "tokens": 0},
            " 4 AM – 8 AM": {"cnt": 0, "cost": 0.0, "tokens": 0},
            " 8 AM – 12 PM": {"cnt": 0, "cost": 0.0, "tokens": 0},
            "12 PM – 4 PM": {"cnt": 0, "cost": 0.0, "tokens": 0},
            " 4 PM – 8 PM": {"cnt": 0, "cost": 0.0, "tokens": 0},
            " 8 PM – 12 AM": {"cnt": 0, "cost": 0.0, "tokens": 0},
        }
        shift_keys = list(shifts.keys())
        for r in rows:
            shift = shift_keys[r["hour"] // 4]
            shifts[shift]["cnt"] += r["cnt"]
            shifts[shift]["cost"] += r["cost"] or 0.0
            shifts[shift]["tokens"] += r["tokens"] or 0
        return [{"shift": k, **v} for k, v in shifts.items()]

    def usage_by_day_of_week(self) -> list[dict]:
        """Return event counts and cost by day of week (0=Sun)."""
        rows = self.conn.execute(
            """SELECT CAST(strftime('%w', timestamp) AS INTEGER) AS dow,
                      COUNT(*) AS cnt,
                      SUM(cost_usd) AS cost,
                      SUM(input_tokens + output_tokens) AS tokens
               FROM token_events
               GROUP BY dow ORDER BY dow"""
        ).fetchall()
        day_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        by_dow = {i: {"day": day_names[i], "cnt": 0, "cost": 0.0, "tokens": 0} for i in range(7)}
        for r in rows:
            by_dow[r["dow"]].update({"cnt": r["cnt"], "cost": r["cost"] or 0.0, "tokens": r["tokens"] or 0})
        return list(by_dow.values())

    def usage_by_week(self, limit: int = 12) -> list[dict]:
        """Return weekly totals, most recent first."""
        rows = self.conn.execute(
            """SELECT strftime('%Y-%W', timestamp) AS week,
                      COUNT(*) AS cnt,
                      SUM(cost_usd) AS cost,
                      SUM(input_tokens + output_tokens) AS tokens
               FROM token_events
               GROUP BY week ORDER BY week DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [{"week": r["week"], "cnt": r["cnt"], "cost": r["cost"] or 0.0, "tokens": r["tokens"] or 0}
                for r in rows]

    def usage_by_month(self) -> list[dict]:
        """Return monthly totals in chronological order."""
        rows = self.conn.execute(
            """SELECT strftime('%Y-%m', timestamp) AS month,
                      COUNT(*) AS cnt,
                      SUM(cost_usd) AS cost,
                      SUM(input_tokens + output_tokens) AS tokens
               FROM token_events
               GROUP BY month ORDER BY month"""
        ).fetchall()
        return [{"month": r["month"], "cnt": r["cnt"], "cost": r["cost"] or 0.0, "tokens": r["tokens"] or 0}
                for r in rows]

    def usage_by_date(self, limit: int = 60) -> list[dict]:
        """Return daily totals for the last N days in chronological order."""
        rows = self.conn.execute(
            """SELECT date(timestamp) AS day,
                      COUNT(*) AS cnt,
                      SUM(cost_usd) AS cost,
                      SUM(input_tokens + output_tokens) AS tokens
               FROM token_events
               GROUP BY day ORDER BY day DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return list(reversed([
            {"date": r["day"], "cnt": r["cnt"], "cost": r["cost"] or 0.0, "tokens": r["tokens"] or 0}
            for r in rows
        ]))

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

    def projects_with_cost(self, limit: int = 20) -> list[dict]:
        """Return all projects with token counts, cost, and session count."""
        rows = self.conn.execute(
            """SELECT s.project,
                      COUNT(DISTINCT s.session_id) AS sessions,
                      SUM(te.input_tokens)  AS input_tokens,
                      SUM(te.output_tokens) AS output_tokens,
                      SUM(te.cost_usd)      AS cost_usd
               FROM token_events te
               JOIN sessions s ON s.session_id = te.session_id
               GROUP BY s.project
               ORDER BY cost_usd DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {
                "project": r["project"],
                "sessions": r["sessions"],
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "total_tokens": r["input_tokens"] + r["output_tokens"],
                "cost_usd": r["cost_usd"],
            }
            for r in rows
        ]

    def cost_by_model(self) -> list[dict]:
        """Return cost and token totals broken down by model."""
        rows = self.conn.execute(
            """SELECT s.model,
                      COUNT(DISTINCT s.session_id) AS sessions,
                      SUM(te.input_tokens)  AS input_tokens,
                      SUM(te.output_tokens) AS output_tokens,
                      SUM(te.cost_usd)      AS cost_usd
               FROM token_events te
               JOIN sessions s ON s.session_id = te.session_id
               GROUP BY s.model
               ORDER BY cost_usd DESC"""
        ).fetchall()
        return [
            {
                "model": r["model"],
                "sessions": r["sessions"],
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "cost_usd": r["cost_usd"],
            }
            for r in rows
        ]

    def session_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS cnt FROM sessions").fetchone()
        return row["cnt"]

    def total_tokens(self) -> tuple[int, int]:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(input_tokens),0) AS i, COALESCE(SUM(output_tokens),0) AS o FROM token_events"
        ).fetchone()
        return (row["i"], row["o"])
