"""Task queue stored in the database (no YAML task files).

Tasks are submitted via the client (`mermaid task add ...`) and picked
up by the server's planner agent.
"""

from __future__ import annotations

import sqlite3

from .config import get_env


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(get_env("MERMAID_DB_PATH"))
    conn.row_factory = sqlite3.Row
    return conn


def init_task_table() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS task (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                definition_of_done TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 100,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                started_at TEXT,
                finished_at TEXT,
                result TEXT
            );
            """
        )


def add_task(
    title: str, description: str, definition_of_done: str, priority: int = 100
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO task (title, description, definition_of_done, priority) VALUES (?, ?, ?, ?)",
            (title, description, definition_of_done, priority),
        )
        return cur.lastrowid


def get_next_task() -> dict | None:
    """Highest-priority pending task (lowest priority number first)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM task WHERE status = 'pending' ORDER BY priority ASC, id ASC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def mark_task_started(task_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE task SET status = 'running', started_at = datetime('now') WHERE id = ?",
            (task_id,),
        )


def mark_task_completed(task_id: int, result: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE task SET status = 'completed', finished_at = datetime('now'), result = ? WHERE id = ?",
            (result, task_id),
        )


def mark_task_failed(task_id: int, result: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE task SET status = 'failed', finished_at = datetime('now'), result = ? WHERE id = ?",
            (result, task_id),
        )


def list_tasks() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, priority, status, created_at, finished_at FROM task ORDER BY status, priority, id"
        ).fetchall()
    return [dict(r) for r in rows]


def get_task(task_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None
