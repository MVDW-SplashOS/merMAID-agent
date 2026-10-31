"""Server-side registry of agent types.

An agent type is a role the multi-agent system can use: it has a system
prompt, an optional task-prompt template, and a tool allow-list. The
built-in types (planner, developer, tester, reviewer, safety) come from the
generic prompts shipped with the server; external scripts can register
additional types — or replace the built-ins — over HTTP:

    POST /agents/<type>   {"system_prompt": ..., "task_template": ..., "tools": [...]}

`system_prompt` and `task_template` support `{{ variable }}` substitution
and `{% include "path" %}` (relative paths resolve against the registering
script's directory when registered through the client helper, otherwise
against the server's prompt root).

The `tools` list restricts which registered tools the agent type may call
(by tool name; empty or omitted = all tools). The `launch_agent` tool's
enum is derived from the registered types, so sub-agents can only be
launched for types that actually exist.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import prompt as prompt_module

_INCLUDE_RE = re.compile(r"^\{%\s*include\s+\"([^\"]+)\"\s*%\}$", re.MULTILINE)
_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# name -> {"system_prompt", "task_template", "tools", "include_root"}
_types: dict[str, dict] = {}


def _render(text: str, variables: dict | None, include_root: Path) -> str:
    variables = variables or {}

    def include_sub(match: re.Match) -> str:
        include_path = Path(match.group(1))
        if not include_path.is_absolute():
            include_path = include_root / include_path
        if include_path.exists():
            return include_path.read_text()
        return f"(missing include: {match.group(1)})"

    text = _INCLUDE_RE.sub(include_sub, text)

    def var_sub(match: re.Match) -> str:
        return str(variables.get(match.group(1), match.group(0)))

    return _VAR_RE.sub(var_sub, text)


def register(
    name: str,
    system_prompt: str,
    task_template: str | None = None,
    tools: list[str] | None = None,
    include_root: str | None = None,
    replace: bool = False,
) -> str:
    """Register (or replace) an agent type. Returns its name."""
    if not name or not name.replace("_", "").isalnum():
        raise ValueError(f"invalid agent type name: {name!r}")
    if not system_prompt or not system_prompt.strip():
        raise ValueError("system_prompt is required")
    if name in _types and not replace:
        raise ValueError(f"agent type already registered: {name}")
    _types[name] = {
        "system_prompt": system_prompt,
        "task_template": task_template,
        "tools": tools or [],
        "include_root": include_root or str(Path(__file__).resolve().parent),
    }
    return name


def unregister(name: str) -> None:
    if name not in _types:
        raise KeyError(f"unknown agent type: {name}")
    del _types[name]


def exists(name: str) -> bool:
    return name in _types


def names() -> list[str]:
    return sorted(_types)


def get(name: str) -> dict:
    if name not in _types:
        raise KeyError(f"unknown agent type: {name}")
    return dict(_types[name])


def system_prompt(name: str) -> str:
    """Rendered system prompt for the agent type."""
    spec = get(name)
    return _render(
        spec["system_prompt"], None, Path(spec["include_root"])
    )


def task_prompt(name: str, task: dict) -> str:
    """Rendered task prompt; falls back to the built-in generic template."""
    spec = get(name)
    template = spec["task_template"] or prompt_module.TASK_TEMPLATE
    return _render(template, task, Path(spec["include_root"]))


def tools_for(name: str) -> list[str]:
    """Tool names the agent type may call (empty = all)."""
    return list(get(name)["tools"])


def describe(name: str) -> dict:
    spec = get(name)
    return {
        "name": name,
        "system_prompt": spec["system_prompt"],
        "task_template": spec["task_template"],
        "tools": spec["tools"],
        "include_root": spec["include_root"],
    }


def list_types() -> list[dict]:
    return [describe(name) for name in names()]
