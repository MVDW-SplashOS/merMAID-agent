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
    POST /tools           -> register a custom tool {schema, callback}
    GET  /tools           -> list registered tools (?builtin=true to include built-ins)
    GET  /tools/<name>    -> full description of one tool
    PUT  /tools/<name>    -> register or replace a custom tool (upsert)
    DELETE /tools/<name>  -> remove a custom tool
    POST /tools/<name>    -> invoke a registered tool (used by callbacks)
    POST /agents/<type>   -> register an agent type {system_prompt, task_template?, tools?}
    GET  /agents          -> list registered agent types
    GET  /agents/<type>   -> full description of one agent type
    PUT  /agents/<type>   -> register or replace an agent type (upsert)
    DELETE /agents/<type> -> remove a registered agent type
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import agent_types, db, tasks, tools_registry
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
            # don't let a failed task's stale context resume into the next task
            db.mark_all_links_complete()
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
        if agent_types.exists(get_agent_type()):
            system = agent_types.system_prompt(get_agent_type())
            user = agent_types.task_prompt(get_agent_type(), task)
        else:
            system = get_system_prompt(get_agent_type())
            user = create_agent_prompt(get_agent_type(), task)
        db.add_context(get_context_id(), "system", system)
        db.add_context(get_context_id(), "user", user)

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
        elif self.path == "/agents":
            self._send(200, {"agents": agent_types.list_types()})
        elif self.path.startswith("/agents/"):
            type_name = self.path.split("?", 1)[0].rsplit("/", 1)[1]
            try:
                self._send(200, {"agent": agent_types.describe(type_name)})
            except KeyError:
                self._send(404, {"error": f"unknown agent type: {type_name}"})
        elif self.path.split("?", 1)[0] == "/tools":
            include_builtin = "builtin=true" in (self.path.split("?", 1) + [""])[1].lower()
            self._send(200, {"tools": tools_registry.list_tools(include_builtin)})
        elif self.path.startswith("/tools/"):
            tool_name = self.path.split("?", 1)[0].rsplit("/", 1)[1]
            try:
                self._send(200, {"tool": tools_registry.get_tool(tool_name)})
            except KeyError:
                self._send(404, {"error": f"unknown tool: {tool_name}"})
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
        elif self.path == "/tools":
            data = self._read_json()
            schema = data.get("schema")
            callback = data.get("callback")
            if not schema or not callback:
                self._send(400, {"error": "schema and callback are required"})
                return
            try:
                name = tools_registry.register(
                    schema,
                    callback,
                    data.get("agents") or [],
                    replace=bool(data.get("replace")),
                )
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
                return
            self._send(201, {"name": name, "status": "registered"})
        elif self.path.startswith("/tools/"):
            tool_name = self.path.rsplit("/", 1)[1]
            data = self._read_json()
            try:
                result = tools_registry.invoke(tool_name, data.get("args") or {})
            except KeyError:
                self._send(404, {"error": f"unknown tool: {tool_name}"})
                return
            except Exception as exc:  # noqa: BLE001
                self._send(500, {"error": str(exc)})
                return
            self._send(200, {"result": result})
        elif self.path.startswith("/agents/"):
            type_name = self.path.rsplit("/", 1)[1]
            data = self._read_json()
            system_prompt = data.get("system_prompt")
            if not system_prompt:
                self._send(400, {"error": "system_prompt is required"})
                return
            try:
                agent_types.register(
                    type_name,
                    system_prompt,
                    data.get("task_template"),
                    data.get("tools") or [],
                    data.get("include_root"),
                    replace=bool(data.get("replace")),
                )
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
                return
            self._send(201, {"name": type_name, "status": "registered"})
        elif self.path == "/stop":
            _stop_requested.set()
            self._send(200, {"status": "stop requested"})
        else:
            self._send(404, {"error": "not found"})

    def do_PUT(self) -> None:  # noqa: N802
        """Upsert a custom tool or agent type (register or replace by name)."""
        if self.path.startswith("/agents/"):
            type_name = self.path.rsplit("/", 1)[1]
            data = self._read_json()
            system_prompt = data.get("system_prompt") or ""
            if not system_prompt:
                self._send(400, {"error": "system_prompt is required"})
                return
            try:
                agent_types.register(
                    type_name,
                    system_prompt,
                    data.get("task_template"),
                    data.get("tools") or [],
                    data.get("include_root"),
                    replace=True,
                )
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
                return
            self._send(200, {"name": type_name, "status": "registered"})
            return
        if not self.path.startswith("/tools/"):
            self._send(404, {"error": "not found"})
            return
        tool_name = self.path.rsplit("/", 1)[1]
        data = self._read_json()
        schema = data.get("schema") or {}
        callback = data.get("callback") or ""
        if not callback:
            self._send(400, {"error": "callback is required"})
            return
        schema.setdefault("function", {})["name"] = tool_name
        try:
            tools_registry.register(
                schema, callback, data.get("agents") or [], replace=True
            )
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
            return
        self._send(200, {"name": tool_name, "status": "registered"})

    def do_DELETE(self) -> None:  # noqa: N802
        """Remove a custom tool or agent type. Built-ins are protected."""
        if self.path.startswith("/agents/"):
            type_name = self.path.rsplit("/", 1)[1]
            try:
                agent_types.unregister(type_name)
            except KeyError:
                self._send(404, {"error": f"unknown agent type: {type_name}"})
                return
            self._send(200, {"name": type_name, "status": "unregistered"})
            return
        if not self.path.startswith("/tools/"):
            self._send(404, {"error": "not found"})
            return
        tool_name = self.path.rsplit("/", 1)[1]
        try:
            tools_registry.unregister(tool_name)
        except KeyError:
            self._send(404, {"error": f"unknown tool: {tool_name}"})
            return
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
            return
        self._send(200, {"name": tool_name, "status": "unregistered"})

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
