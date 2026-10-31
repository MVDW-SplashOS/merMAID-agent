"""Mermaid client: talk to the background server.

Usage:
    mermaid start              start the server in the background
    mermaid status             server health + current task
    mermaid task add <desc>    queue a task (opens $EDITOR for details)
    mermaid task list          list tasks
    mermaid task show <id>     show a task with its result
    mermaid stop               ask the server to stop the current run
    mermaid shutdown           stop the server process
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

from .config import get_env


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"http://{get_env('MERMAID_HOST')}:{get_env('MERMAID_PORT')}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # surface provider/API errors from the server
        try:
            detail = json.loads(exc.read()).get("error", str(exc))
        except Exception:
            detail = str(exc)
        print(f"error: {detail}")
        sys.exit(1)
    except urllib.error.URLError:
        print("error: server is not running (start it with: mermaid start)")
        sys.exit(1)


def _server_running() -> bool:
    """Non-fatal health probe used by `start` and `shutdown`."""
    url = f"http://{get_env('MERMAID_HOST')}:{get_env('MERMAID_PORT')}/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _start_server() -> None:
    if _server_running():
        print("server is already running")
        return

    log_path = str(get_env("MERMAID_DB_PATH")).rsplit("/", 1)[0] + "/mermaid.log"
    with open(log_path, "ab") as log:
        subprocess.Popen(
            [sys.executable, "-m", "mermaid.server"],
            stdout=log,
            stderr=log,
            start_new_session=True,  # detach: survives terminal close
        )

    # wait briefly for the daemon to bind its port
    import time

    for _ in range(20):
        if _server_running():
            print(f"server started in the background (log: {log_path})")
            return
        time.sleep(0.25)
    print(f"warning: server did not respond yet; check {log_path}")


def _shutdown_server() -> None:
    if not _server_running():
        print("server is not running")
        return
    out = subprocess.run(
        ["pgrep", "-f", "python3 -m mermaid[.]server"],
        capture_output=True,
        text=True,
    ).stdout.split()
    if not out:
        print("warning: health endpoint is up but no server process found")
        return
    for pid in out:
        subprocess.run(["kill", pid])
    print("server stopped")


def _add_task(args: argparse.Namespace) -> None:
    if args.description:
        # join all positional words into one long description
        description = " ".join(args.description).strip()
    else:
        # no description given: open $EDITOR for a long-form task
        editor = os.environ.get("EDITOR", "vi")
        import tempfile

        with tempfile.NamedTemporaryFile("w+", suffix=".md") as tmp:
            tmp.write(
                "# Task description\n\n\n"
                "# Definition of done (below the marker)\n"
                "# --- definition of done ---\n"
            )
            tmp.flush()
            subprocess.run([editor, tmp.name], check=True)
            tmp.seek(0)
            content = tmp.read()
        if "# --- definition of done ---" in content:
            description, _, dod = content.partition("# --- definition of done ---")
        else:
            description, dod = content, ""
        description = description.replace("# Task description", "").strip()
        dod = dod.strip()

    if not description:
        print("error: empty task description")
        sys.exit(1)

    if args.description:
        dod = args.definition_of_done or "The task is fully implemented and validated."
    else:
        dod = args.definition_of_done or dod

    payload = {
        "title": args.title or description.splitlines()[0][:60] if description else "",
        "description": description,
        "definition_of_done": dod,
        "priority": args.priority,
    }
    result = _request("POST", "/tasks", payload)
    print(f"queued task #{result['id']}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="mermaid")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("start", help="start the server in the background")
    sub.add_parser("status", help="show server health and current task")

    task = sub.add_parser("task", help="manage tasks")
    task_sub = task.add_subparsers(dest="task_command", required=True)

    add = task_sub.add_parser("add", help="queue a new task")
    add.add_argument(
        "description",
        nargs="*",
        help="task description as one long string (quoted or unquoted); $EDITOR if omitted",
    )
    add.add_argument("-t", "--title", help="short title (defaults to first line)")
    add.add_argument("-d", "--definition-of-done", help="definition of done")
    add.add_argument("-p", "--priority", type=int, default=100, help="lower runs first")

    task_sub.add_parser("list", help="list tasks")
    show = task_sub.add_parser("show", help="show one task")
    show.add_argument("id", type=int)

    sub.add_parser("stop", help="ask the server to stop the current run")
    sub.add_parser("shutdown", help="stop the server process")

    args = parser.parse_args()

    if args.command == "start":
        _start_server()
    elif args.command == "status":
        print(json.dumps(_request("GET", "/health"), indent=2))
    elif args.command == "task":
        if args.task_command == "add":
            _add_task(args)
        elif args.task_command == "list":
            print(json.dumps(_request("GET", "/tasks")["tasks"], indent=2))
        elif args.task_command == "show":
            print(json.dumps(_request("GET", f"/tasks/{args.id}")["task"], indent=2))
    elif args.command == "stop":
        print(json.dumps(_request("POST", "/stop"), indent=2))
    elif args.command == "shutdown":
        _shutdown_server()


if __name__ == "__main__":
    main()
