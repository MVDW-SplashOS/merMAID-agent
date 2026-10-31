"""Generic fallback prompts.

The server ships with minimal generic prompts so it works out of the box.
Domain-specific agent types (with their own system prompts and task
templates) are registered at runtime over HTTP — see `agent_types.py` and
the `/agents` API.
"""

from __future__ import annotations

# Minimal generic system prompts, one per built-in agent type. An external
# script can replace any of them by registering the same type name.
FALLBACKS: dict[str, str] = {
    "planner": (
        "# Planner Agent\n\n"
        "You coordinate the end-to-end execution of tasks: analyze the task, "
        "survey the environment, decompose it into subtasks with acceptance "
        "criteria, delegate each subtask via `launch_agent` (developer, "
        "tester, reviewer), integrate results, and call `done` with proof "
        "the task meets its definition of done."
    ),
    "developer": (
        "# Developer Agent\n\n"
        "You implement the subtask you were given exactly as specified, "
        "verify it (launch a tester agent), assess quality (launch a "
        "reviewer agent), and call `done` with evidence."
    ),
    "tester": (
        "# Tester Agent\n\n"
        "You verify the change: run the relevant builds, parse-checks, and "
        "tests, report concrete command output as evidence, and call `done` "
        "with a summary of results."
    ),
    "reviewer": (
        "# Reviewer Agent\n\n"
        "You review the change for correctness, conventions, quality, and "
        "scope, then call `done` with a verdict (approve / request changes) "
        "and actionable feedback."
    ),
    "safety": (
        "# Safety Agent\n\n"
        "You approve or deny shell commands before they execute on the "
        "host. Deny destructive, insecure, or unintended operations "
        "(rm -rf /, dd to devices, fork bombs, curl|bash, unsandboxed "
        "sudo, mkfs/shutdown/reboot); approve read-only and scoped "
        "build/parse commands. Call `bash_approve_deny` with your decision."
    ),
}

# Generic task prompt used when an agent type has no task_template.
TASK_TEMPLATE = (
    "# Task: {{ title }}\n\n"
    "**Description:**\n{{ description }}\n\n"
    "**Definition of Done:**\n{{ definition_of_done }}\n"
)


def get_system_prompt(agent_type: str) -> str:
    prompt = FALLBACKS.get(agent_type)
    if prompt is None:
        raise KeyError(f"no fallback system prompt for agent type {agent_type}")
    return prompt


def create_agent_prompt(agent_type: str, task: dict) -> str:
    template = TASK_TEMPLATE
    if agent_type == "safety":
        return (
            "Assess the following shell command for safety before it is "
            "executed on the host.\n\nCommand:\n\n```\n{{ command }}\n```\n"
        ).replace("{{ command }}", str(task.get("command", "")))
    return (
        template.replace("{{ title }}", str(task.get("title", "")))
        .replace("{{ description }}", str(task.get("description", "")))
        .replace(
            "{{ definition_of_done }}", str(task.get("definition_of_done", ""))
        )
    )
