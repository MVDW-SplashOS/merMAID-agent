## Agent Call Types

When using `launch_agent`:

- `developer` — implements a subtask. Provide: exact files/paths, specifications, acceptance criteria.
- `tester` — verifies functionality. Provide: what to test, expected behavior, validation commands.
- `reviewer` — reviews code quality. Provide: which files changed and the task's acceptance criteria.
- `safety` — only used internally for `bash` command approval; do not launch manually.

Always give the sub-agent enough context (file locations, prior findings) that
it does not need to re-research the project.
