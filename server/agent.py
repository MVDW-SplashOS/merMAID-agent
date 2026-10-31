"""The agent execution loop: request -> tool calls -> repeat."""

from __future__ import annotations

import json

from . import db
from .providers import send_request
from .state import get_agent_type, get_context_id
from .registry import registry


def execute_agent() -> None:
    agent_type = get_agent_type()
    context_id = get_context_id()

    context = db.get_context(context_id)

    # Drop a trailing assistant message so the provider doesn't reject it.
    if context and context[-1]["role"] == "assistant":
        db.delete_message_from_context(context_id, len(context) - 1)
        context = db.get_context(context_id)

    # Guard against runaway loops: if the model keeps making tool calls
    # without finishing, stop after this many iterations.
    max_iterations = 200
    for _ in range(max_iterations):
        response = send_request(agent_type, context, registry.tools_for(agent_type))
        content = response.get("content", "")
        tool_calls = response.get("tool_calls") or []

        if content:
            db.add_context(context_id, "assistant", content)

        if not tool_calls:
            break  # model finished without tool calls

        for tool_call in tool_calls:
            function = tool_call.get("function", {})
            name = function.get("name", "")
            raw_args = function.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {}

            print(f"--> tool: {name} {args}")
            result = registry.run(name, args)
            # custom tools return a JSON string; built-ins return a dict
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    result = {"result": result}

            # bash/launch_agent/done switch the active context; re-read it.
            context_id = get_context_id()
            agent_type = get_agent_type()
            context = db.get_context(context_id)

            if result.get("action") == "continue":
                break  # safety agent already appended the tool result

            # Context-switching tools (bash / launch_agent / done) hand
            # control to another agent; stop this loop so the new agent's
            # own execute_agent() run takes over.
            if result.get("action") == "return":
                return

            # Read-only tools: feed the result back so the model sees it.
            db.add_context(context_id, "tool", json.dumps(result), name)
    else:
        print(
            f"! agent {agent_type} hit the {max_iterations}-iteration limit; "
            "stopping to avoid an infinite loop"
        )
