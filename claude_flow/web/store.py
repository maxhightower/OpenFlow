"""WebStore: extends SchedulerStore with projects table for multi-project support."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from claude_flow.scheduler.store import SchedulerStore

_WEB_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id    TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    dag_json      TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    is_archived   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cross_project_deps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_project_id TEXT NOT NULL,
    from_task_id    TEXT NOT NULL,
    to_project_id   TEXT NOT NULL,
    to_task_id      TEXT NOT NULL,
    UNIQUE(from_project_id, from_task_id, to_project_id, to_task_id)
);
"""


class WebStore(SchedulerStore):
    """SchedulerStore extended with project management tables."""

    def __init__(self, db_path: Path | None = None) -> None:
        # Override parent __init__ to add check_same_thread=False for FastAPI
        self.db_path = db_path or Path.home() / ".claudeflow" / "claudeflow.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Run parent schema
        from claude_flow.scheduler.store import _SCHEMA
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # Run web-specific schema
        self._conn.executescript(_WEB_SCHEMA)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.commit()

    # -- Projects --------------------------------------------------------------

    def create_project(
        self,
        project_id: str,
        name: str,
        description: str = "",
        dag_json: str = "{}",
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO projects (project_id, name, description, dag_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, name, description, dag_json, now, now),
        )
        self._conn.commit()
        return self.get_project(project_id)

    def get_project(self, project_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_project(row)

    def list_projects(self, include_archived: bool = False) -> list[dict]:
        if include_archived:
            rows = self._conn.execute(
                "SELECT * FROM projects ORDER BY updated_at DESC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM projects WHERE is_archived = 0 ORDER BY updated_at DESC"
            ).fetchall()
        return [_row_to_project(r) for r in rows]

    def update_project(self, project_id: str, **kwargs) -> dict | None:
        allowed = {"name", "description", "dag_json", "is_archived"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return self.get_project(project_id)
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [project_id]
        self._conn.execute(
            f"UPDATE projects SET {set_clause} WHERE project_id = ?", values
        )
        self._conn.commit()
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> bool:
        cur = self._conn.execute(
            "UPDATE projects SET is_archived = 1, updated_at = ? WHERE project_id = ?",
            (datetime.now(timezone.utc).isoformat(), project_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def save_project_dag(self, project_id: str, dag_json: str) -> None:
        self._conn.execute(
            "UPDATE projects SET dag_json = ?, updated_at = ? WHERE project_id = ?",
            (dag_json, datetime.now(timezone.utc).isoformat(), project_id),
        )
        self._conn.commit()

    # -- Cross-project dependencies -------------------------------------------

    def add_cross_dep(
        self,
        from_project_id: str,
        from_task_id: str,
        to_project_id: str,
        to_task_id: str,
    ) -> int:
        cur = self._conn.execute(
            """INSERT OR IGNORE INTO cross_project_deps
               (from_project_id, from_task_id, to_project_id, to_task_id)
               VALUES (?, ?, ?, ?)""",
            (from_project_id, from_task_id, to_project_id, to_task_id),
        )
        self._conn.commit()
        return cur.lastrowid

    def list_cross_deps(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM cross_project_deps"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "from_project_id": r["from_project_id"],
                "from_task_id": r["from_task_id"],
                "to_project_id": r["to_project_id"],
                "to_task_id": r["to_task_id"],
            }
            for r in rows
        ]

    def delete_cross_dep(self, dep_id: int) -> bool:
        cur = self._conn.execute(
            "DELETE FROM cross_project_deps WHERE id = ?", (dep_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0


def _row_to_project(row: sqlite3.Row) -> dict:
    return {
        "project_id": row["project_id"],
        "name": row["name"],
        "description": row["description"],
        "dag_json": json.loads(row["dag_json"]) if row["dag_json"] else {},
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "is_archived": bool(row["is_archived"]),
    }
