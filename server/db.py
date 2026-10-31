"""SQLite-backed context and link store (replaces Astra's MySQL layer)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from .config import get_env

AGENT_ROLES = ("user", "assistant", "system", "tool")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(get_env("MERMAID_DB_PATH"))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS context (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                context_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_name TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_context_id ON context(context_id);
            CREATE TABLE IF NOT EXISTS link (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                context_id TEXT UNIQUE NOT NULL,
                agent_type TEXT NOT NULL,
                parent_context_id TEXT,
                complete INTEGER NOT NULL DEFAULT 0
            );
            """
        )


def create_context_id() -> str:
    return str(uuid.uuid4())


def add_context(
    context_id: str, role: str, content: str, tool_name: str | None = None
) -> None:
    if not context_id or role not in AGENT_ROLES or not content:
        raise ValueError("invalid context parameters")
    with _connect() as conn:
        last = conn.execute(
            "SELECT role FROM context WHERE context_id = ? ORDER BY id DESC LIMIT 1",
            (context_id,),
        ).fetchone()
        if last and last["role"] == "assistant" and role == "assistant":
            return  # skip duplicate assistant messages
        conn.execute(
            "INSERT INTO context (context_id, role, content, tool_name) VALUES (?, ?, ?, ?)",
            (context_id, role, content, tool_name),
        )


def get_context(context_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content, tool_name FROM context WHERE context_id = ? ORDER BY id ASC",
            (context_id,),
        ).fetchall()
    messages = []
    for row in rows:
        msg: dict[str, Any] = {"role": row["role"], "content": row["content"]}
        if row["tool_name"]:
            msg["tool_name"] = row["tool_name"]
        messages.append(msg)
    return messages


def has_context(context_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM context WHERE context_id = ? LIMIT 1", (context_id,)
        ).fetchone()
    return row is not None


def delete_message_from_context(context_id: str, index: int) -> None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM context WHERE context_id = ? ORDER BY id ASC LIMIT 1 OFFSET ?",
            (context_id, index),
        ).fetchone()
        if row:
            conn.execute("DELETE FROM context WHERE id = ?", (row["id"],))


def insert_link(
    context_id: str, agent_type: str, parent_context_id: str | None = None
) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO link (context_id, agent_type, parent_context_id) VALUES (?, ?, ?)",
            (context_id, agent_type, parent_context_id),
        )


def get_link(context_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT context_id, agent_type, parent_context_id, complete FROM link WHERE context_id = ?",
            (context_id,),
        ).fetchone()
    return dict(row) if row else None


def mark_link_complete(context_id: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE link SET complete = 1 WHERE context_id = ?", (context_id,))


def mark_all_links_complete() -> None:
    """Close out every unfinished link (used when a task fails)."""
    with _connect() as conn:
        conn.execute("UPDATE link SET complete = 1 WHERE complete = 0")


def get_incomplete_link() -> dict | None:
    """Most recent unfinished link, used to resume after a crash."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT context_id, agent_type FROM link WHERE complete = 0 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def dump_json(value: Any) -> str:
    return json.dumps(value, indent=2)
