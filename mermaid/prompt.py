"""Load markdown prompts with simple {{ variable }} substitution."""

from __future__ import annotations

import re
from pathlib import Path

from .config import PROJECT_ROOT

PROMPTS_ROOT = Path(__file__).resolve().parent / "prompts"

_INCLUDE_RE = re.compile(r"^\{%\s*include\s+\"([^\"]+)\"\s*%\}$", re.MULTILINE)
_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _render(text: str, variables: dict | None = None) -> str:
    variables = variables or {}

    def include_sub(match: re.Match) -> str:
        include_path = PROMPTS_ROOT / match.group(1)
        if include_path.exists():
            return include_path.read_text()
        return f"(missing include: {match.group(1)})"

    text = _INCLUDE_RE.sub(include_sub, text)

    def var_sub(match: re.Match) -> str:
        return str(variables.get(match.group(1), match.group(0)))

    return _VAR_RE.sub(var_sub, text)


def get_system_prompt(agent_type: str) -> str:
    path = PROMPTS_ROOT / "system" / f"{agent_type}.md"
    if not path.exists():
        raise FileNotFoundError(f"no system prompt for agent type {agent_type}")
    return _render(path.read_text())


def create_agent_prompt(agent_type: str, task: dict) -> str:
    path = PROMPTS_ROOT / "agent" / f"{agent_type}.md"
    if not path.exists():
        raise FileNotFoundError(f"no agent prompt for agent type {agent_type}")
    return _render(path.read_text(), task)
