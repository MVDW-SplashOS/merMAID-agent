## Coding Conventions

- Match the style of the surrounding code; keep changes minimal and focused.
- Python: stdlib-first, type hints, `pathlib` for filesystem work.
- Shell scripts: `set -euo pipefail`, quote variables, POSIX-compatible where possible.
- YAML/JSON configs: validate with a parse-check before finishing.
- Never commit in the environment root; each symlinked repo commits its own changes.
