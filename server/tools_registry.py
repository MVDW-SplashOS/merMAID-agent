"""Server-side registry of custom tools.

Custom tools are registered over HTTP by external Python scripts:

    POST /tools  {"schema": <OpenAI function schema>, "callback": <URL>}

The schema must be an OpenAI-style function schema; the callback is the URL
the server POSTs tool arguments to when the agent calls the tool. Callbacks
must respond with JSON; the JSON is fed back to the agent as the tool
result. Callback URLs are restricted to localhost.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .registry import registry

CALLBACK_TIMEOUT = 60 * 30  # long tool runs are expected


def _is_localhost(url: str) -> bool:
    host = urllib.request.urlsplit(url).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


def register(
    schema: dict,
    callback: str,
    agents: list[str] | None = None,
    replace: bool = False,
) -> str:
    """Register a custom tool backed by an HTTP callback. Returns its name."""
    if not isinstance(schema, dict) or "function" not in schema:
        raise ValueError("schema must be an OpenAI-style function schema")
    function = schema["function"]
    name = function.get("name")
    if not name:
        raise ValueError("schema.function.name is required")
    if not callback or not _is_localhost(callback):
        raise ValueError("callback must be a localhost URL")

    def invoke(_args: dict, _name: str = name, _callback: str = callback) -> str:
        payload = json.dumps({"tool": _name, "args": _args}).encode()
        req = urllib.request.Request(
            _callback,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=CALLBACK_TIMEOUT) as resp:
                body = resp.read().decode()
        except urllib.error.URLError as exc:
            return json.dumps({"error": f"tool callback failed: {exc}"})
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError:
            return body  # non-JSON callback output is passed through as text
        # unwrap the callback envelope: the agent gets the tool's value
        if isinstance(decoded, dict) and set(decoded) <= {"result", "error"}:
            return json.dumps(decoded.get("result", decoded))
        return json.dumps(decoded)

    invoke.callback_url = callback  # exposed by get_tool()

    registry.register(
        schema=schema, fn=invoke, agents=agents, custom=True, replace=replace
    )
    return name


def unregister(name: str) -> None:
    """Remove a custom tool from the registry."""
    registry.unregister(name)


def get_tool(name: str) -> dict:
    """Full description of one tool: schema, agents, callback, custom flag."""
    tool = registry.get(name)
    return {
        "name": tool.name,
        "schema": tool.schema,
        "agents": tool.agents,
        "custom": tool.custom,
        "callback": getattr(tool.fn, "callback_url", None),
    }


def list_tools(include_builtin: bool = False) -> list[dict]:
    """Registered tools (name, agents, custom flag, and schema if custom)."""
    tools = (
        registry.custom_tools()
        if not include_builtin
        else [tool for tool in registry.all_tools()]
    )
    return [
        {
            "name": tool.name,
            "agents": tool.agents,
            "custom": tool.custom,
            "schema": tool.schema if tool.custom else None,
        }
        for tool in tools
    ]


def invoke(name: str, args: dict) -> str:
    """Invoke a registered tool (used by the callback script itself)."""
    return registry.run(name, args)
