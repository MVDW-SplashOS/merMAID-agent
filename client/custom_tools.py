"""Helper for custom tool scripts.

A custom tool script is a plain Python file that defines tools and calls
`register_tool` for each. Register them with the running server:

    mermaid tool register my_tools.py

or run the script directly (`python3 my_tools.py`) — it talks to the
merMAID server over HTTP (via the `lib` client library), so it can live
anywhere.

Example script:

    from client.custom_tools import register_tool

    MY_SCHEMA = {
        "type": "function",
        "function": {
            "name": "hello",
            "description": "Say hello to someone.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    }

    def handle(args: dict) -> dict:
        return {"greeting": f"hello {args['name']}"}

    register_tool(MY_SCHEMA, handle, agents=["developer", "tester"])

The script process must stay alive while the tool is in use: it hosts a
local HTTP callback server that the merMAID server POSTs tool arguments to.
`mermaid tool register` keeps the script running in the background for you.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lib import MermaidClient

_HANDLERS: dict[str, callable] = {}
_callback_httpd: ThreadingHTTPServer | None = None
_callback_port: int = 0
_client: MermaidClient | None = None


def _server() -> MermaidClient:
    global _client
    if _client is None:
        _client = MermaidClient()
    return _client


def _start_callback_server() -> None:
    """Start the local HTTP server the merMAID server will call back to."""

    class Router(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            tool_name = self.path.rsplit("/", 1)[1]
            handler = _HANDLERS.get(tool_name)
            if handler is None:
                body = json.dumps({"error": f"unknown tool: {tool_name}"}).encode()
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            try:
                result = handler(data.get("args") or {})
                body, code = json.dumps({"result": result}).encode(), 200
            except Exception as exc:  # noqa: BLE001
                body, code = json.dumps({"error": str(exc)}).encode(), 500
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):  # silence request logging
            pass

    global _callback_httpd, _callback_port
    _callback_httpd = ThreadingHTTPServer(("127.0.0.1", 0), Router)
    _callback_port = _callback_httpd.server_address[1]
    threading.Thread(target=_callback_httpd.serve_forever, daemon=True).start()


def register_tool(
    schema: dict,
    handler: callable,
    agents: list[str] | None = None,
    replace: bool = False,
) -> str:
    """Register a tool with the merMAID server.

    `schema` is an OpenAI-style function schema; `handler` is a local
    function receiving the tool arguments dict and returning any
    JSON-serializable result. `agents` restricts which agent types may call
    the tool (None = all agents). With `replace=True`, an existing custom
    tool of the same name is overwritten (hot-reload friendly).
    """
    name = schema.get("function", {}).get("name", "")
    if not name:
        raise ValueError("schema.function.name is required")
    if name in _HANDLERS and not replace:
        raise ValueError(f"tool already defined in this script: {name}")

    if _callback_httpd is None:
        _start_callback_server()

    _HANDLERS[name] = handler
    _server().register_tool(
        schema,
        f"http://127.0.0.1:{_callback_port}/tool-callbacks/{name}",
        agents=agents,
        replace=replace,
    )
    return name


def unregister_tool(name: str) -> None:
    """Remove a custom tool from the server's registry."""
    _HANDLERS.pop(name, None)
    _server().unregister_tool(name)


def wait() -> None:
    """Block forever, keeping the callback server alive."""
    threading.Event().wait()
