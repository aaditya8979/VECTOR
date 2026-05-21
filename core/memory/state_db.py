"""
StateDB — SQLite-backed persistent state for the TSDC agent.
Survives process restarts, account switches, and machine reboots.
All data is project-local: stored in <project_root>/.codeagent/state.db
"""
from __future__ import annotations

import sqlite3
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     TEXT    NOT NULL,
    file_path   TEXT    NOT NULL,
    func_name   TEXT    NOT NULL,
    description TEXT    NOT NULL,
    diff_hash   TEXT,
    timestamp   REAL    NOT NULL,
    task_id     INTEGER
);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    description  TEXT    NOT NULL,
    target_file  TEXT    NOT NULL,
    target_func  TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',
    created_at   REAL    NOT NULL,
    updated_at   REAL    NOT NULL,
    iterations   INTEGER NOT NULL DEFAULT 0,
    token_budget_used INTEGER DEFAULT 0,
    compression_ratio REAL DEFAULT 0.0,
    result       TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL,
    metric_name TEXT    NOT NULL,
    value       REAL    NOT NULL,
    recorded_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_changes_node ON changes(node_id);
CREATE INDEX IF NOT EXISTS idx_changes_time ON changes(timestamp);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""


class StateDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(db_path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(SCHEMA)
        self._con.commit()

    # ── Change log ────────────────────────────────────────────────────────────

    def log_change(
        self,
        node_id:     str,
        file_path:   str,
        func_name:   str,
        description: str,
        diff_hash:   Optional[str] = None,
        task_id:     Optional[int] = None,
    ):
        self._con.execute(
            """INSERT INTO changes
               (node_id, file_path, func_name, description, diff_hash, timestamp, task_id)
               VALUES (?,?,?,?,?,?,?)""",
            (node_id, file_path, func_name, description, diff_hash, time.time(), task_id),
        )
        self._con.commit()

    def get_recent_changes(self, node_ids: List[str], days: int = 14) -> List[Dict]:
        since     = time.time() - days * 86400
        placeholders = ",".join("?" * len(node_ids))
        rows = self._con.execute(
            f"""SELECT node_id, description, timestamp
                FROM changes
                WHERE node_id IN ({placeholders}) AND timestamp > ?
                ORDER BY timestamp DESC LIMIT 20""",
            (*node_ids, since),
        ).fetchall()
        result = []
        for row in rows:
            days_ago = int((time.time() - row["timestamp"]) / 86400)
            result.append({
                "node_id":     row["node_id"],
                "description": row["description"],
                "days_ago":    days_ago,
            })
        return result

    # ── Task management ───────────────────────────────────────────────────────

    def create_task(self, description: str, target_file: str, target_func: str) -> int:
        now = time.time()
        cur = self._con.execute(
            """INSERT INTO tasks
               (description, target_file, target_func, status, created_at, updated_at)
               VALUES (?,?,?,'pending',?,?)""",
            (description, target_file, target_func, now, now),
        )
        self._con.commit()
        return cur.lastrowid

    def update_task(self, task_id: int, **kwargs):
        kwargs["updated_at"] = time.time()
        sets   = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [task_id]
        self._con.execute(f"UPDATE tasks SET {sets} WHERE id = ?", values)
        self._con.commit()

    def get_last_incomplete_task(self) -> Optional[Dict]:
        row = self._con.execute(
            """SELECT * FROM tasks
               WHERE status IN ('pending', 'in_progress')
               ORDER BY updated_at DESC LIMIT 1"""
        ).fetchone()
        return dict(row) if row else None

    def get_task(self, task_id: int) -> Optional[Dict]:
        row = self._con.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row else None

    # ── Metrics ───────────────────────────────────────────────────────────────

    def record_metric(self, task_id: int, name: str, value: float):
        self._con.execute(
            "INSERT INTO metrics (task_id, metric_name, value, recorded_at) VALUES (?,?,?,?)",
            (task_id, name, value, time.time()),
        )
        self._con.commit()

    def get_metrics_summary(self) -> Dict[str, Any]:
        rows = self._con.execute(
            """SELECT metric_name,
                      AVG(value)  as avg,
                      MIN(value)  as min,
                      MAX(value)  as max,
                      COUNT(*)    as n
               FROM metrics GROUP BY metric_name"""
        ).fetchall()
        return {r["metric_name"]: dict(r) for r in rows}

    def get_task_metrics(self, task_id: int) -> List[Dict]:
        rows = self._con.execute(
            "SELECT * FROM metrics WHERE task_id = ? ORDER BY recorded_at",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Session state (for resume) ────────────────────────────────────────────

    def set_session(self, key: str, value: Any):
        self._con.execute(
            "INSERT OR REPLACE INTO session (key, value) VALUES (?,?)",
            (key, json.dumps(value)),
        )
        self._con.commit()

    def get_session(self, key: str, default: Any = None) -> Any:
        row = self._con.execute(
            "SELECT value FROM session WHERE key = ?", (key,)
        ).fetchone()
        return json.loads(row["value"]) if row else default

    def close(self):
        self._con.close()