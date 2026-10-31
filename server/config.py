"""Configuration for the Mermaid.

All configuration comes from environment variables, optionally loaded from a
`.env` file (repo root or /etc/mermaid/.env). No YAML config files are
used — `.env` is the single source of truth for credentials and settings.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "provider": "llamacpp",
    "provider_url": "http://localhost:8080",
    "model": "qwen3.5:27b-q4_K_M",
    "num_ctx": "98304",
    "db_path": str(PROJECT_ROOT / "mermaid.db"),
    "environment_root": str(
        Path.home() / "Documents" / "GitHub" / "splashos_agent_enviroment"
    ),
    "host": "127.0.0.1",
    "port": "7331",
    "debug": "false",
    "safety_agent": "false",
}

_FILE_CACHE: dict[str, str] | None = None


def _parse_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser: KEY=VALUE lines, # comments, optional quotes."""
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _load_env_file() -> dict[str, str]:
    global _FILE_CACHE
    if _FILE_CACHE is not None:
        return _FILE_CACHE
    _FILE_CACHE = {}
    for candidate in (PROJECT_ROOT / ".env", Path("/etc/mermaid/.env")):
        if candidate.exists():
            _FILE_CACHE = _parse_env_file(candidate)
            break
    return _FILE_CACHE


def get_env(key: str, optional: bool = True):
    """Resolve MERMAID_* from real environment vars, then .env, then defaults."""
    file_cfg = _load_env_file()
    file_key = key.replace("MERMAID_", "").lower()

    value = os.environ.get(key)
    if value is None:
        value = file_cfg.get(key) or file_cfg.get(file_key)
    if value is None:
        value = DEFAULTS.get(file_key)

    if value is None and not optional:
        raise RuntimeError(f"Environment variable {key} is not set")

    if isinstance(value, str):
        if value.lower() in ("true", "1"):
            value = True
        elif value.lower() in ("false", "0"):
            value = False
        elif value.isdigit():
            value = int(value)
    return value
