"""Mermaid CLI: thin command-line wrapper around the lib (mermaidclient).

All HTTP connection logic lives in `lib`; this module only maps argv to
library calls and formats output.

Usage:
    mermaid start              start the server in the background
    mermaid status             server health + current task
    mermaid task add <desc>    queue a task (opens $EDITOR for details)
    mermaid task list          list tasks
    mermaid task show <id>     show a task with its result
    mermaid stop               ask the server to stop the current run
    mermaid shutdown           stop the server process
    mermaid tool register <script.py>
                               register custom tools with the server
    mermaid tool list [--all]  list registered tools
    mermaid tool show <name>   show one tool's full schema
    mermaid tool remove <name> remove a custom tool
    mermaid agent register <script.py>
                               register agent types with the server
    mermaid agent list         list registered agent types
    mermaid agent show <name>  show one agent type's prompts
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

from lib import MermaidClient, MermaidServerError


def _client() -> MermaidClient:
    return MermaidClient()


def _run(fn) -> None:
    """Run a library call, printing server errors and exiting non-zero."""
    try:
        fn()
    except MermaidServerError as exc:
        print(f"error: {exc}")
        sys.exit(1)


def _start(args: argparse.Namespace) -> None:
    client = _client()
    if client.is_running():
        print("server is already running")
        return
    if client.start_server():
        print(f"server started in the background (log: {client._log_path()})")
    else:
        print(f"warning: server did not respond yet; check {client._log_path()}")


def _status(args: argparse.Namespace) -> None:
    _run(lambda: print(json.dumps(_client().health(), indent=2)))


def _add_task(args: argparse.Namespace) -> None:
    client = _client()
    if args.description:
        description = " ".join(args.description).strip()
        dod = args.definition_of_done
    else:
        # no description given: open $EDITOR for a long-form task
        editor = os.environ.get("EDITOR", "vi")
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
        dod = (dod or "").strip()

    if not description:
        print("error: empty task description")
        sys.exit(1)

    def go() -> None:
        result = client.add_task(
            description,
            title=args.title,
            definition_of_done=dod,
            priority=args.priority,
        )
        print(f"queued task #{result['id']}")

    _run(go)


def _list_tasks(args: argparse.Namespace) -> None:
    _run(lambda: print(json.dumps(_client().list_tasks(), indent=2)))


def _show_task(args: argparse.Namespace) -> None:
    _run(lambda: print(json.dumps(_client().get_task(args.id), indent=2)))


def _stop(args: argparse.Namespace) -> None:
    _run(lambda: print(json.dumps(_client().stop_current_run(), indent=2)))


def _shutdown(args: argparse.Namespace) -> None:
    client = _client()
    if not client.is_running():
        print("server is not running")
        return
    if client.shutdown_server():
        print("server stopped")
    else:
        print("warning: health endpoint is up but no server process found")


def _register_tools(args: argparse.Namespace) -> None:
    """Run a custom-tool script in the background.

    The script registers its tools with the server over HTTP and then keeps
    running: it hosts the local callback server the tool invocations are
    routed through. A pidfile lets a later run of the same script replace
    (restart) a previous one.
    """
    script = os.path.abspath(args.script)
    if not os.path.isfile(script):
        print(f"error: no such script: {script}")
        sys.exit(1)

    # replace a previously started instance of the same script
    pidfile = f"/tmp/mermaid-tool-{os.path.basename(script)}.pid"
    if os.path.exists(pidfile):
        try:
            old_pid = int(open(pidfile).read().strip())
            os.kill(old_pid, signal.SIGTERM)
            print(f"stopped previous instance (pid {old_pid})")
        except (ValueError, ProcessLookupError, PermissionError):
            pass
        os.remove(pidfile)

    log_path = pidfile.replace(".pid", ".log")
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(
            [sys.executable, script],
            stdout=log,
            stderr=log,
            start_new_session=True,  # detach: survives terminal close
        )
    with open(pidfile, "w") as f:
        f.write(str(proc.pid))

    import time

    time.sleep(0.5)
    if proc.poll() is not None:
        print(f"error: script exited immediately; see {log_path}")
        sys.exit(1)
    print(f"tools registered from {script} (pid {proc.pid}, log: {log_path})")


def _register_agents(args: argparse.Namespace) -> None:
    """Run an agent-type registration script.

    Unlike tools, agent types don't need a callback server, so the script
    can simply exit after registering.
    """
    script = os.path.abspath(args.script)
    if not os.path.isfile(script):
        print(f"error: no such script: {script}")
        sys.exit(1)
    env = os.environ.copy()
    repo_root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, script], env=env)
    sys.exit(result.returncode)


def _list_agents(args: argparse.Namespace) -> None:
    def go() -> None:
        agents = _client().list_agent_types()
        print(json.dumps([a["name"] for a in agents], indent=2))

    _run(go)


def _show_agent(args: argparse.Namespace) -> None:
    def go() -> None:
        print(json.dumps(_client().get_agent_type(args.name), indent=2))

    _run(go)


def _list_tools(args: argparse.Namespace) -> None:
    _run(
        lambda: print(
            json.dumps(_client().list_tools(include_builtin=args.all), indent=2)
        )
    )


def _show_tool(args: argparse.Namespace) -> None:
    _run(lambda: print(json.dumps(_client().get_tool(args.name), indent=2)))


def _remove_tool(args: argparse.Namespace) -> None:
    def go() -> None:
        _client().unregister_tool(args.name)
        print(json.dumps({"name": args.name, "status": "unregistered"}, indent=2))

    _run(go)


def main() -> None:
    parser = argparse.ArgumentParser(prog="mermaid")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("start", help="start the server in the background")
    p.set_defaults(fn=_start)
    p = sub.add_parser("status", help="show server health and current task")
    p.set_defaults(fn=_status)

    task = sub.add_parser("task", help="manage tasks")
    task_sub = task.add_subparsers(dest="task_command", required=True)

    p = task_sub.add_parser("add", help="queue a new task")
    p.add_argument(
        "description",
        nargs="*",
        help="task description as one long string (quoted or unquoted); $EDITOR if omitted",
    )
    p.add_argument("-t", "--title", help="short title (defaults to first line)")
    p.add_argument("-d", "--definition-of-done", help="definition of done")
    p.add_argument("-p", "--priority", type=int, default=100, help="lower runs first")
    p.set_defaults(fn=_add_task)

    p = task_sub.add_parser("list", help="list tasks")
    p.set_defaults(fn=_list_tasks)
    p = task_sub.add_parser("show", help="show one task")
    p.add_argument("id", type=int)
    p.set_defaults(fn=_show_task)

    tool = sub.add_parser("tool", help="manage custom tools")
    tool_sub = tool.add_subparsers(dest="tool_command", required=True)

    p = tool_sub.add_parser(
        "register", help="run a Python script that registers custom tools"
    )
    p.add_argument("script", help="path to the tool script to execute")
    p.set_defaults(fn=_register_tools)

    p = tool_sub.add_parser(
        "list", help="list registered tools (--all includes built-ins)"
    )
    p.add_argument("--all", action="store_true", help="include built-in tools")
    p.set_defaults(fn=_list_tools)

    p = tool_sub.add_parser("show", help="show one tool's full schema")
    p.add_argument("name")
    p.set_defaults(fn=_show_tool)

    p = tool_sub.add_parser("remove", help="remove a custom tool from the server")
    p.add_argument("name")
    p.set_defaults(fn=_remove_tool)

    agent = sub.add_parser("agent", help="manage agent types")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)

    p = agent_sub.add_parser(
        "register", help="run a Python script that registers agent types"
    )
    p.add_argument("script", help="path to the agent-type script to execute")
    p.set_defaults(fn=_register_agents)

    p = agent_sub.add_parser("list", help="list registered agent types")
    p.set_defaults(fn=_list_agents)

    p = agent_sub.add_parser("show", help="show one agent type's prompts")
    p.add_argument("name")
    p.set_defaults(fn=_show_agent)

    p = sub.add_parser("stop", help="ask the server to stop the current run")
    p.set_defaults(fn=_stop)
    p = sub.add_parser("shutdown", help="stop the server process")
    p.set_defaults(fn=_shutdown)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
