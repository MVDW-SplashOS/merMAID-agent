"""LLM provider abstraction: llama.cpp (default), Ollama, and any
OpenAI-compatible endpoint (vLLM, LM Studio, OpenRouter, ...)."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from .config import get_env

TIMEOUT_SECONDS = 60 * 60  # long generations are expected


def _auth_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = get_env("MERMAID_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _post(url: str, payload: dict) -> urllib.request.Request:
    return urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=_auth_headers(), method="POST"
    )


def _stream_ollama(url: str, payload: dict) -> dict:
    """Ollama /api/chat streaming: accumulate content + tool calls."""
    content_parts: list[str] = []
    tool_calls: list[dict] = []
    try:
        with urllib.request.urlopen(
            _post(url, payload), timeout=TIMEOUT_SECONDS
        ) as response:
            for raw_line in response:
                line = raw_line.decode().strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = chunk.get("message", {})
                if message.get("content"):
                    content_parts.append(message["content"])
                    sys.stdout.write(message["content"])
                    sys.stdout.flush()
                if message.get("tool_calls"):
                    tool_calls = message["tool_calls"]
                if chunk.get("done"):
                    break
    except urllib.error.URLError as exc:
        raise RuntimeError(f"provider request failed: {exc}") from exc

    print()  # newline after streamed output
    return {"content": "".join(content_parts), "tool_calls": tool_calls}


def _openai_chat(url: str, payload: dict) -> dict:
    """OpenAI-compatible /v1/chat/completions (non-streaming for tool reliability)."""
    try:
        with urllib.request.urlopen(
            _post(url, payload), timeout=TIMEOUT_SECONDS
        ) as response:
            data = json.loads(response.read())
    except urllib.error.URLError as exc:
        raise RuntimeError(f"provider request failed: {exc}") from exc

    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message", {})
    content = message.get("content") or ""
    if content:
        sys.stdout.write(content)
        sys.stdout.flush()
        print()

    tool_calls = message.get("tool_calls") or []
    # normalize OpenAI tool call format to the internal one
    normalized = []
    for call in tool_calls:
        fn = call.get("function", {})
        normalized.append(
            {
                "function": {
                    "name": fn.get("name"),
                    "arguments": fn.get("arguments", "{}"),
                }
            }
        )
    return {"content": content, "tool_calls": normalized}


def _openai_messages(messages: list[dict]) -> list[dict]:
    """Strip internal fields (tool_name) not understood by OpenAI-style APIs."""
    return [{k: v for k, v in m.items() if k != "tool_name"} for m in messages]


def send_request(agent_type: str, messages: list[dict], tools: list[dict]) -> dict:
    provider = get_env("MERMAID_PROVIDER")
    provider_url = str(get_env("MERMAID_PROVIDER_URL")).rstrip("/")
    model = get_env("MERMAID_MODEL")
    num_ctx = int(get_env("MERMAID_NUM_CTX"))

    if provider == "llamacpp":
        payload = {
            "model": model,
            "messages": _openai_messages(messages),
            "tools": tools,
            "stream": False,
        }
        return _openai_chat(f"{provider_url}/v1/chat/completions", payload)

    if provider == "openai":
        payload = {
            "model": model,
            "messages": _openai_messages(messages),
            "tools": tools,
            "stream": False,
        }
        return _openai_chat(f"{provider_url}/chat/completions", payload)

    if provider == "ollama":
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": True,
            "think": True,
            "options": {"num_ctx": num_ctx},
        }
        return _stream_ollama(f"{provider_url}/api/chat", payload)

    raise RuntimeError(f"unsupported provider: {provider}")
