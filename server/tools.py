"""Built-in tools, registered into the dynamic tool registry.

The agent runs directly on the host (no container), so every tool has
unrestricted global access to the filesystem and shell. External scripts
can add custom tools at runtime via the server's /tools HTTP API.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import agent_types, db
from .config import get_env
from .registry import registry
from .state import get_agent_type, get_context_id, set_agent_type, set_context_id


def environment_root() -> Path:
    return Path(str(get_env("MERMAID_ENVIRONMENT_ROOT"))).resolve()


def resolve_path(path: str) -> Path:
    """Resolve a tool path; relative paths are anchored at the environment
    root (the server process may run with a different cwd)."""
    target = Path(path)
    return target if target.is_absolute() else environment_root() / target


# --- implementations -------------------------------------------------------


def _list_directory(path: str = "") -> str:
    if isinstance(path, dict):
        path = path.get("path", "")
    target = resolve_path(path) if path else environment_root()
    entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
    lines = []
    for entry in entries:
        suffix = "/" if entry.is_dir() else ""
        lines.append(entry.name + suffix)
    return "\n".join(lines) or "(empty directory)"


def _create_directory(path: str) -> str:
    resolve_path(path).mkdir(parents=True, exist_ok=True)
    return f"created {path}"


def _read_file(
    path: str, start_line: int | None = None, end_line: int | None = None
) -> str:
    if isinstance(path, dict):
        path = path.get("path", "")
    lines = resolve_path(path).read_text().splitlines()
    start = (start_line or 1) - 1
    end = end_line if end_line is not None else len(lines)
    selected = lines[start:end]
    return "\n".join(f"{i + start + 1:5d} | {line}" for i, line in enumerate(selected))


def _write_file(path: str, content: str) -> str:
    target = resolve_path(path)
    if target.parent != Path("."):
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return f"wrote {len(content)} chars to {path}"


def _execute_shell(command: str) -> str:
    result = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=60 * 30
    )
    return db.dump_json(
        {
            "command": command,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exitCode": result.returncode,
        }
    )


def _bash(args) -> dict:
    if not isinstance(args, dict):
        args = {"command": args}
    command = args.get("command", "")
    """Route through a safety sub-agent when enabled, else execute directly."""
    if get_env("MERMAID_SAFETY_AGENT"):
        db.add_context(get_context_id(), "tool", command, "bash")
        new_context_id = db.create_context_id()
        db.insert_link(new_context_id, "safety", get_context_id())
        set_context_id(new_context_id)
        set_agent_type("safety")
        if agent_types.exists("safety"):
            system = agent_types.system_prompt("safety")
            user = agent_types.task_prompt("safety", {"command": command})
        else:
            from .prompt import create_agent_prompt, get_system_prompt

            system = get_system_prompt("safety")
            user = create_agent_prompt("safety", {"command": command})
        db.add_context(new_context_id, "system", system)
        db.add_context(new_context_id, "user", user, "bash")
        return {"action": "return"}

    # safety agent disabled: execute immediately
    db.add_context(get_context_id(), "tool", _execute_shell(command), "bash")
    return {"action": "continue"}


def _bash_approve_deny(args) -> dict:
    if not isinstance(args, dict):
        args = {"decision": args}
    decision = args.get("decision", "")
    reason = args.get("reason", "")
    parent = db.get_link(get_context_id())
    parent_id = parent["parent_context_id"]
    parent_context = db.get_context(parent_id)
    command = parent_context[-1]["content"]

    set_agent_type(parent["agent_type"])
    set_context_id(parent_id)
    db.delete_message_from_context(parent_id, len(parent_context) - 1)
    db.mark_link_complete(get_context_id())

    if decision == "approve":
        outcome = db.dump_json(json.loads(_execute_shell(command)))
    else:
        outcome = db.dump_json(
            {
                "approve": False,
                "command": command,
                "reason": reason or "denied by safety agent",
            }
        )
    db.add_context(parent_id, "tool", outcome, "bash")
    return {"action": "continue"}


def _launch_agent(args) -> dict:
    if not isinstance(args, dict):
        args = {}
    agent_type = args.get("agent_type", "")
    context = args.get("context", "")
    from . import agent_types

    new_context_id = db.create_context_id()
    db.insert_link(new_context_id, agent_type, get_context_id())
    set_context_id(new_context_id)
    set_agent_type(agent_type)

    if agent_types.exists(agent_type):
        system = agent_types.system_prompt(agent_type)
    else:
        from .prompt import get_system_prompt

        system = get_system_prompt(agent_type)
    db.add_context(new_context_id, "system", system)
    db.add_context(new_context_id, "user", context)
    return {"action": "return"}


def _done(args) -> dict:
    if not isinstance(args, dict):
        args = {"message": args}
    message = args.get("message", "")
    current_id = get_context_id()
    current_type = get_agent_type()
    link = db.get_link(current_id)
    parent_id = link and link["parent_context_id"]

    if not parent_id:
        raise SystemExit(0)  # root agent finished

    parent = db.get_link(parent_id)
    set_agent_type(parent["agent_type"])
    set_context_id(parent_id)
    db.add_context(
        parent_id,
        "tool",
        db.dump_json({"success": True, "agent_type": current_type, "reply": message}),
        "launch_agent",
    )
    db.mark_link_complete(current_id)
    return {"action": "return"}


# --- registration ----------------------------------------------------------


def _launch_agent_schema() -> dict:
    """Build the launch_agent schema with the enum of registered agent types.

    Built fresh on each tools_for() call: at import time the agent-type
    registry is still empty, and llama.cpp rejects an empty enum with a 500.
    """
    return {
        "type": "function",
        "function": {
            "name": "launch_agent",
            "description": "Launch a sub-agent of the given type with a descriptive first message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_type": {
                        "type": "string",
                        "enum": agent_types.names(),
                        "description": "Type of agent to launch.",
                    },
                    "context": {
                        "type": "string",
                        "description": "First message for the agent; be fully descriptive so it need not re-research.",
                    },
                },
                "required": ["agent_type", "context"],
            },
        },
    }


class _LaunchAgentTool:
    """Schema proxy so registry.tools_for() sees a current enum."""

    def __init__(self) -> None:
        self.fn = _launch_agent
        self.agents = ["planner", "developer"]
        self.custom = False

    @property
    def schema(self) -> dict:
        return _launch_agent_schema()


def register_builtin_tools() -> None:
    r = registry

    r.register(
        {
            "type": "function",
            "function": {
                "name": "done",
                "description": "Signal that the current task is finished, with proof it meets the definition of done.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Completion message with evidence.",
                        }
                    },
                    "required": ["message"],
                },
            },
        },
        _done,
        ["planner", "developer", "tester", "reviewer"],
    )
    r.register(
        {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "List the contents of a directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute path. Empty means the environment root.",
                        }
                    },
                },
            },
        },
        _list_directory,
        ["planner", "developer", "tester", "reviewer"],
    )
    r.register(
        {
            "type": "function",
            "function": {
                "name": "create_directory",
                "description": "Create a directory (including parents) at the given path.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute path to create."}
                    },
                    "required": ["path"],
                },
            },
        },
        _create_directory,
        ["developer"],
    )
    r.register(
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file's contents, optionally a line range (1-indexed).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path of the file to read."},
                        "start_line": {
                            "type": "integer",
                            "description": "Optional start line (1-indexed).",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "Optional end line (1-indexed, inclusive).",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        _read_file,
        ["planner", "developer", "tester", "reviewer"],
    )
    r.register(
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write (or overwrite) a file with the given content.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path of the file to write."},
                        "content": {"type": "string", "description": "Full file content."},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        _write_file,
        ["developer", "tester"],
    )
    r.register(
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Execute a shell command on the host and return stdout/stderr/exit code.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "The shell command to execute."}
                    },
                    "required": ["command"],
                },
            },
        },
        _bash,
        ["developer", "tester"],
    )
    r.register(
        {
            "type": "function",
            "function": {
                "name": "bash_approve_deny",
                "description": "Safety agent: approve or deny a pending bash command.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "decision": {
                            "type": "string",
                            "enum": ["approve", "deny"],
                            "description": "The safety decision.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Reason for denial (omit for approvals).",
                        },
                    },
                    "required": ["decision"],
                },
            },
        },
        _bash_approve_deny,
        ["safety"],
    )
    r.register(
        _launch_agent_schema(),
        _launch_agent,
        ["planner", "developer"],
    )
    # replace the static registration with a schema proxy so the enum stays
    # current as agent types are (un)registered at runtime
    r._tools["launch_agent"] = _LaunchAgentTool()


register_builtin_tools()
