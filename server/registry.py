"""Dynamic tool registry.

Tools are registered at runtime instead of being hardcoded: the built-in
tools register themselves when this package is imported, and profiles can
register additional tools from their own modules via
``register_tools(registry)``.

Each tool declares which agent types may call it, so agent tool sets are
derived from the registry rather than a static table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Tool:
    name: str
    schema: dict
    fn: Callable
    agents: list[str] = field(default_factory=list)
    custom: bool = False  # registered at runtime over HTTP


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        schema: dict,
        fn: Callable,
        agents: list[str] | None = None,
        custom: bool = False,
        replace: bool = False,
    ) -> str:
        """Register a tool. `schema` is the OpenAI-style function schema;
        `agents` lists the agent types allowed to call it (None = all).
        With `replace=True`, an existing custom tool of the same name is
        overwritten (built-in tools are protected). Returns the tool name."""
        name = schema["function"]["name"]
        existing = self._tools.get(name)
        if existing is not None:
            if not replace or not existing.custom:
                raise ValueError(f"tool already registered: {name}")
        self._tools[name] = Tool(
            name=name, schema=schema, fn=fn, agents=agents or [], custom=custom
        )
        return name

    def unregister(self, name: str) -> None:
        """Remove a custom tool. Built-in tools cannot be removed."""
        tool = self.get(name)
        if not tool.custom:
            raise ValueError(f"{name} is a built-in tool and cannot be removed")
        del self._tools[name]

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all_tools(self) -> list["Tool"]:
        return list(self._tools.values())

    def custom_tools(self) -> list["Tool"]:
        return [tool for tool in self._tools.values() if tool.custom]

    def is_custom(self, name: str) -> bool:
        return self.get(name).custom

    def tools_for(self, agent_type: str) -> list[dict]:
        """Schemas of all tools the given agent type may call.

        Intersection of (a) tools that allow this agent type and (b) the
        agent type's own allow-list (empty = all tools).
        """
        from . import agent_types

        allowed = agent_types.tools_for(agent_type) if agent_types.exists(agent_type) else []
        return [
            tool.schema
            for name, tool in self._tools.items()
            if (not tool.agents or agent_type in tool.agents)
            and (not allowed or name in allowed)
        ]

    def run(self, name: str, args: dict) -> dict:
        return self.get(name).fn(args)


registry = ToolRegistry()
