"""HTTP client for the merMAID server.

Every request the CLI or a custom-tool script makes goes through
`MermaidClient`: connection setup, error handling, and process management
(start/shutdown of the background daemon) all live here so consumers never
touch urllib themselves.

This package is standalone: it has no dependency on the server codebase.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_TIMEOUT = 10


def _get_env(key: str, default: str) -> str:
    """Resolve MERMAID_* from real environment vars, then .env, then default.

    Mirrors the server's config lookup so both sides agree on host/port
    without sharing code.
    """
    value = os.environ.get(key)
    if value:
        return value
    file_key = key.replace("MERMAID_", "").lower()
    for candidate in (Path.cwd() / ".env", Path("/etc/mermaid/.env")):
        if candidate.exists():
            for raw in candidate.read_text().splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip().lower() in (key.lower(), file_key):
                    return v.strip().strip('"').strip("'")
            break
    return default


class MermaidServerError(RuntimeError):
    """The server returned an error (HTTP 4xx/5xx) or is unreachable."""


class MermaidClient:
    """Connection to a merMAID server daemon.

    Args:
        host: server host; defaults to MERMAID_HOST (or .env / 127.0.0.1).
        port: server port; defaults to MERMAID_PORT (or .env / 7331).
        timeout: per-request timeout in seconds.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.host = str(
            host if host is not None else _get_env("MERMAID_HOST", "127.0.0.1")
        )
        self.port = int(port if port is not None else _get_env("MERMAID_PORT", "7331"))
        self.timeout = timeout

    # --- low-level HTTP -----------------------------------------------------

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        """Send a request and return the decoded JSON response.

        Raises MermaidServerError on HTTP errors or if the server is
        unreachable.
        """
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get("error", str(exc))
            except Exception:  # noqa: BLE001
                detail = str(exc)
            raise MermaidServerError(detail) from exc
        except urllib.error.URLError as exc:
            raise MermaidServerError(
                f"server is not running at {self.base_url} "
                f"(start it with: mermaid start)"
            ) from exc

    def get(self, path: str) -> dict:
        return self.request("GET", path)

    def post(self, path: str, payload: dict | None = None) -> dict:
        return self.request("POST", path, payload)

    def put(self, path: str, payload: dict) -> dict:
        return self.request("PUT", path, payload)

    def delete(self, path: str) -> dict:
        return self.request("DELETE", path)

    # --- process management ---------------------------------------------------

    def is_running(self) -> bool:
        """True if a server answers on the configured host/port."""
        try:
            urllib.request.urlopen(f"{self.base_url}/health", timeout=2)
            return True
        except Exception:  # noqa: BLE001
            return False

    def start_server(self, wait_seconds: float = 5.0) -> bool:
        """Start the server daemon in the background if not already running.

        Returns True if a server is running afterwards.
        """
        if self.is_running():
            return True

        log_path = self._log_path()
        with open(log_path, "ab") as log:
            subprocess.Popen(
                [sys.executable, "-m", "server.server"],
                stdout=log,
                stderr=log,
                start_new_session=True,  # detach: survives terminal close
            )
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if self.is_running():
                return True
            time.sleep(0.25)
        return False

    def shutdown_server(self) -> bool:
        """Stop the server daemon. Returns True if a process was killed."""
        if not self.is_running():
            return False
        out = subprocess.run(
            ["pgrep", "-f", "python3 -m server[.]server"],
            capture_output=True,
            text=True,
        ).stdout.split()
        for pid in out:
            subprocess.run(["kill", pid])
        return bool(out)

    def _log_path(self) -> str:
        db_path = _get_env("MERMAID_DB_PATH", "mermaid.db")
        return str(Path(db_path).resolve().parent / "mermaid.log")

    # --- health / tasks -------------------------------------------------------

    def health(self) -> dict:
        return self.get("/health")

    def add_task(
        self,
        description: str,
        title: str | None = None,
        definition_of_done: str | None = None,
        priority: int = 100,
    ) -> dict:
        payload = {
            "title": title or description.splitlines()[0][:60],
            "description": description,
            "definition_of_done": definition_of_done
            or "The task is fully implemented and validated.",
            "priority": priority,
        }
        return self.post("/tasks", payload)

    def list_tasks(self) -> list[dict]:
        return self.get("/tasks")["tasks"]

    def get_task(self, task_id: int) -> dict:
        return self.get(f"/tasks/{task_id}")["task"]

    def stop_current_run(self) -> dict:
        return self.post("/stop")

    # --- tools ----------------------------------------------------------------

    def list_tools(self, include_builtin: bool = False) -> list[dict]:
        suffix = "?builtin=true" if include_builtin else ""
        return self.get("/tools" + suffix)["tools"]

    def get_tool(self, name: str) -> dict:
        return self.get(f"/tools/{name}")["tool"]

    def register_tool(
        self,
        schema: dict,
        callback: str,
        agents: list[str] | None = None,
        replace: bool = False,
    ) -> str:
        """Register a custom tool backed by a localhost HTTP callback."""
        payload = {
            "schema": schema,
            "callback": callback,
            "agents": agents or [],
            "replace": replace,
        }
        return self.post("/tools", payload)["name"]

    def upsert_tool(
        self,
        name: str,
        schema: dict,
        callback: str,
        agents: list[str] | None = None,
    ) -> None:
        """Register or replace a tool by name (hot-reload friendly)."""
        self.put(
            f"/tools/{name}",
            {"schema": schema, "callback": callback, "agents": agents or []},
        )

    def unregister_tool(self, name: str) -> None:
        self.delete(f"/tools/{name}")

    def invoke_tool(self, name: str, args: dict) -> str:
        return self.post(f"/tools/{name}", {"args": args})["result"]

    # --- agent types ----------------------------------------------------------

    def list_agent_types(self) -> list[dict]:
        return self.get("/agents")["agents"]

    def get_agent_type(self, name: str) -> dict:
        return self.get(f"/agents/{name}")["agent"]

    def register_agent_type(
        self,
        name: str,
        system_prompt: str,
        task_template: str | None = None,
        tools: list[str] | None = None,
        include_root: str | None = None,
        replace: bool = False,
    ) -> str:
        """Register an agent type (role) with its prompts and tool list.

        `system_prompt`/`task_template` support {{ variable }} substitution
        and {% include "file" %} (relative paths resolve against
        `include_root`). `tools` restricts which tools the type may call
        (None/empty = all). With `replace=True` an existing type of the same
        name is overwritten (e.g. to override a built-in).
        """
        payload = {
            "system_prompt": system_prompt,
            "task_template": task_template,
            "tools": tools or [],
            "include_root": include_root,
            "replace": replace,
        }
        return self.post(f"/agents/{name}", payload)["name"]

    def upsert_agent_type(
        self,
        name: str,
        system_prompt: str,
        task_template: str | None = None,
        tools: list[str] | None = None,
        include_root: str | None = None,
    ) -> None:
        """Register or replace an agent type by name (hot-reload friendly)."""
        self.put(
            f"/agents/{name}",
            {
                "system_prompt": system_prompt,
                "task_template": task_template,
                "tools": tools or [],
                "include_root": include_root,
            },
        )

    def unregister_agent_type(self, name: str) -> None:
        self.delete(f"/agents/{name}")


def client_from_env(**overrides) -> MermaidClient:
    """Build a MermaidClient from MERMAID_HOST/MERMAID_PORT env vars.

    Kept for convenience in scripts that already set the env explicitly.
    """
    host = overrides.get("host", os.environ.get("MERMAID_HOST"))
    port = overrides.get("port", os.environ.get("MERMAID_PORT"))
    kwargs = {}
    if host:
        kwargs["host"] = host
    if port:
        kwargs["port"] = int(port)
    return MermaidClient(**kwargs)


# re-exported so scripts can signal cleanly without importing signal
SIGTERM = signal.SIGTERM
