"""Global agent state (current context id and agent type)."""

from __future__ import annotations

_context_id: str = ""
_agent_type: str = ""


def set_context_id(value: str) -> None:
    global _context_id
    _context_id = value


def set_agent_type(value: str) -> None:
    global _agent_type
    _agent_type = value


def get_context_id() -> str:
    return _context_id


def get_agent_type() -> str:
    return _agent_type
