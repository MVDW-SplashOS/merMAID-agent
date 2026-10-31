"""Tool definitions and implementations.

The agent runs directly on the host (no container), so every tool has
unrestricted global access to the filesystem and shell.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from . import db
from .config import get_env
from .state import get_agent_type, get_context_id, set_agent_type, set_context_id

TOOL_LIST: dict[str, dict] = {
    "done": {
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
    "list_directory": {
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
    "create_directory": {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": "Create a directory (including parents) at the given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to create.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    "read_file": {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file's contents, optionally a line range (1-indexed).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the file to read.",
                    },
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
    "write_file": {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write (or overwrite) a file with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the file to write.",
                    },
                    "content": {"type": "string", "description": "Full file content."},
                },
                "required": ["path", "content"],
            },
        },
    },
    "bash": {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a shell command on the host and return stdout/stderr/exit code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    }
                },
                "required": ["command"],
            },
        },
    },
    "bash_approve_deny": {
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
    "launch_agent": {
        "type": "function",
        "function": {
            "name": "launch_agent",
            "description": "Launch a sub-agent of the given type with a descriptive first message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_type": {
                        "type": "string",
                        "enum": ["developer", "tester", "reviewer", "safety"],
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
    },
}

AGENT_TOOLS: dict[str, list[str]] = {
    "planner": ["list_directory", "read_file", "launch_agent", "done"],
    "developer": [
        "list_directory",
        "create_directory",
        "read_file",
        "write_file",
        "bash",
        "launch_agent",
        "done",
    ],
    "tester": ["bash", "read_file", "list_directory", "write_file", "done"],
    "reviewer": ["read_file", "list_directory", "done"],
    "safety": ["bash_approve_deny"],
}


def environment_root() -> Path:
    return Path(str(get_env("MERMAID_ENVIRONMENT_ROOT"))).resolve()


# --- tool implementations -------------------------------------------------


def _tool_list_directory(path: str = "") -> str:
    target = Path(path) if path else environment_root()
    entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
    lines = []
    for entry in entries:
        suffix = "/" if entry.is_dir() else ""
        lines.append(entry.name + suffix)
    return "\n".join(lines) or "(empty directory)"


def _tool_create_directory(path: str) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    return f"created {path}"


def _tool_read_file(
    path: str, start_line: int | None = None, end_line: int | None = None
) -> str:
    lines = Path(path).read_text().splitlines()
    start = (start_line or 1) - 1
    end = end_line if end_line is not None else len(lines)
    selected = lines[start:end]
    return "\n".join(f"{i + start + 1:5d} | {line}" for i, line in enumerate(selected))


def _tool_write_file(path: str, content: str) -> str:
    target = Path(path)
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


def _tool_bash(command: str) -> dict:
    """Route through a safety sub-agent when enabled, else execute directly."""
    if get_env("MERMAID_SAFETY_AGENT"):
        db.add_context(get_context_id(), "tool", command, "bash")
        new_context_id = db.create_context_id()
        db.insert_link(new_context_id, "safety", get_context_id())
        set_context_id(new_context_id)
        set_agent_type("safety")
        from .prompt import create_agent_prompt, get_system_prompt

        db.add_context(new_context_id, "system", get_system_prompt("safety"))
        db.add_context(
            new_context_id,
            "user",
            create_agent_prompt("safety", {"command": command}),
            "bash",
        )
        return {"action": "return"}

    # safety agent disabled: execute immediately
    db.add_context(get_context_id(), "tool", _execute_shell(command), "bash")
    return {"action": "continue"}


def _tool_bash_approve_deny(decision: str, reason: str = "") -> dict:
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


def _tool_launch_agent(agent_type: str, context: str) -> dict:
    new_context_id = db.create_context_id()
    db.insert_link(new_context_id, agent_type, get_context_id())
    set_context_id(new_context_id)
    set_agent_type(agent_type)
    from .prompt import get_system_prompt

    db.add_context(new_context_id, "system", get_system_prompt(agent_type))
    db.add_context(new_context_id, "user", context)
    return {"action": "return"}


def _tool_done(message: str) -> dict:
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


_IMPLEMENTATIONS = {
    "list_directory": _tool_list_directory,
    "create_directory": _tool_create_directory,
    "read_file": _tool_read_file,
    "write_file": _tool_write_file,
    "bash": _tool_bash,
    "bash_approve_deny": _tool_bash_approve_deny,
    "launch_agent": _tool_launch_agent,
    "done": _tool_done,
}


def run_tool(name: str, args: dict) -> dict:
    if name not in _IMPLEMENTATIONS:
        raise ValueError(f"unknown tool: {name}")
    return _IMPLEMENTATIONS[name](**args)


def tools_for(agent_type: str) -> list[dict]:
    return [TOOL_LIST[name] for name in AGENT_TOOLS[agent_type]]
