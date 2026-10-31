"""Mermaid server: a background daemon that runs the agent loop.

The server owns the agent execution (planner -> developer/tester/reviewer
with safety-gated bash) and exposes a small HTTP API on 127.0.0.1 so the
client can submit tasks and query status without keeping a shell open.

Endpoints:
    GET  /health          -> {"status": "ok", "busy": bool, "current_task": ...}
    POST /tasks           -> {"title", "description", "definition_of_done", "priority"?}
    GET  /tasks           -> list of tasks
    GET  /tasks/<id>      -> single task with result
    POST /stop            -> request the current run to stop gracefully
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db, tasks
from .agent import execute_agent
from .config import get_env
from .prompt import create_agent_prompt, get_system_prompt
from .state import get_agent_type, get_context_id, set_agent_type, set_context_id

_stop_requested = threading.Event()
_busy = threading.Event()
_current_task: dict | None = None
_lock = threading.Lock()


def _run_agent_loop() -> None:
    """Background worker: pick up pending tasks and run the planner agent."""
    global _current_task
    while True:
        task = tasks.get_next_task()
        if task is None:
            _busy.clear()
            _current_task = None
            # sleep until a new task arrives (or stop is requested)
            _stop_requested.wait(timeout=5)
            _stop_requested.clear()
            continue

        with _lock:
            _current_task = task
        _busy.set()
        tasks.mark_task_started(task["id"])
        print(f"=== running task #{task['id']}: {task['title'][:80]}")

        try:
            _execute_task(task)
            tasks.mark_task_completed(task["id"], "root agent finished")
        except SystemExit:
            tasks.mark_task_completed(task["id"], "root agent finished")
        except Exception as exc:  # noqa: BLE001
            print(f"! task #{task['id']} failed: {exc}")
            tasks.mark_task_failed(task["id"], str(exc))
        finally:
            _busy.clear()
            with _lock:
                _current_task = None


def _execute_task(task: dict) -> None:
    resume = db.get_incomplete_link()
    if resume:
        set_context_id(resume["context_id"])
        set_agent_type(resume["agent_type"])
    else:
        set_context_id(db.create_context_id())
        set_agent_type("planner")
        db.insert_link(get_context_id(), get_agent_type())

    if not db.has_context(get_context_id()):
        db.add_context(get_context_id(), "system", get_system_prompt(get_agent_type()))
        db.add_context(
            get_context_id(), "user", create_agent_prompt(get_agent_type(), task)
        )

    execute_agent()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(
                200,
                {
                    "status": "ok",
                    "busy": _busy.is_set(),
                    "current_task": _current_task,
                    "stop_requested": _stop_requested.is_set(),
                },
            )
        elif self.path == "/tasks":
            self._send(200, {"tasks": tasks.list_tasks()})
        elif self.path.startswith("/tasks/"):
            try:
                task_id = int(self.path.rsplit("/", 1)[1])
            except ValueError:
                self._send(404, {"error": "invalid task id"})
                return
            task = tasks.get_task(task_id)
            if task is None:
                self._send(404, {"error": "task not found"})
            else:
                self._send(200, {"task": task})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/tasks":
            data = self._read_json()
            title = data.get("title") or (data.get("description") or "")[:60]
            description = data.get("description", "")
            if not description:
                self._send(400, {"error": "description is required"})
                return
            task_id = tasks.add_task(
                title,
                description,
                data.get(
                    "definition_of_done", "The task is fully implemented and validated."
                ),
                int(data.get("priority", 100)),
            )
            self._send(201, {"id": task_id, "status": "queued"})
        elif self.path == "/stop":
            _stop_requested.set()
            self._send(200, {"status": "stop requested"})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt: str, *args) -> None:  # silence request logging
        pass


def main() -> None:
    db.init_db()
    tasks.init_task_table()

    worker = threading.Thread(target=_run_agent_loop, daemon=True)
    worker.start()

    host = str(get_env("MERMAID_HOST"))
    port = int(get_env("MERMAID_PORT"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"mermaid server listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")


if __name__ == "__main__":
    main()
