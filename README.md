# merMAID

A generic Python multi-agent coding agent. The architecture is modeled on
[astra](https://github.com/MVDW-Java/astra) (planner / developer / tester /
reviewer agents with tool calling), rewritten in Python. Domain behavior is
added through **custom tools** (see below), so the same server can drive a
SplashOS build environment or, eventually, act as the in-OS assistant for
SplashOS itself (like Copilot in Windows).

It runs **directly on the host** — no container, no sandbox — with full
global access to the filesystem and shell.

## Architecture

**Three-package split.** The codebase is separated into `/server`, `/client`,
and `/lib`:

```
server/   the daemon: agent loop, tool registry, LLM providers, SQLite state
client/   the CLI (`mermaid ...`) and the custom-tool script helper
lib/      standalone connection library (MermaidClient) — no server deps
```

The server is a background daemon that owns the agent loop; the client is a
thin CLI that talks to it over a local HTTP API through `lib`. You can close
the terminal — the server keeps working.

```
client (CLI) ──lib──HTTP──▶ server (daemon, 127.0.0.1:7331)
                               └── agent loop: planner ──▶ developer ──▶ tester / reviewer
```

- **planner** — decomposes a task and coordinates sub-agents.
- **developer** — implements subtasks (read/write files, run commands).
- **tester** — validates changes (builds, parse-checks, tests).
- **reviewer** — read-only code review.
- **safety** *(optional)* — approves/denies every bash command before it runs.
  Disabled by default; enable with `MERMAID_SAFETY_AGENT=true` in `.env`.
  Since the agent is not containerized, consider enabling it — but be aware
  approval ultimately comes from an LLM.

State (conversation context per agent, parent/child agent links, task queue)
is persisted in SQLite (`mermaid.db`), so an interrupted run resumes where it
left off.

## Requirements

- Python 3.10+
- PyYAML (`pip install -r requirements.txt`)
- A local [llama.cpp](https://github.com/ggml-org/llama.cpp) server (default),
  any OpenAI-compatible endpoint, or [Ollama](https://ollama.com)

## Usage

```bash
cp .env.example .env   # edit credentials/settings first

mermaid start          # start the server in the background (survives terminal close)
mermaid status         # health + current task
mermaid task add "Add a BuildScript for lz4"   # queue a task
mermaid task list      # see the queue
mermaid task show 1    # result of a finished task
mermaid stop           # ask the server to stop the current run
mermaid shutdown       # stop the server process
mermaid tool register my_tools.py   # register custom tools
```

Run the modules directly if you don't install a wrapper script:

```bash
python3 -m client start
python3 -m server.server   # run the server in the foreground
```

## Custom tools

Custom tools are plain Python scripts that register themselves with the
running server over HTTP — no config files, no environment variables:

```python
# my_tools.py
from client.custom_tools import register_tool

SCHEMA = {
    "type": "function",
    "function": {
        "name": "hello",
        "description": "Say hello to someone.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
}

def handle(args: dict) -> dict:
    return {"greeting": f"hello {args['name']}"}

register_tool(SCHEMA, handle, agents=["developer", "tester"])
```

Register it with the server:

```bash
mermaid tool register my_tools.py
```

The script runs in the background and hosts a local callback server the
merMAID server POSTs tool arguments to; its JSON return value is fed back
to the agent as the tool result. `agents` restricts which agent types may
call the tool (omit for all). Re-running `mermaid tool register` with the
same script replaces the previous instance. Callbacks must be localhost
URLs.

### Python library (`lib`)

All connection logic — HTTP requests, error handling, and starting/stopping
the daemon — lives in the standalone `lib` package. The CLI and the custom
tool helper are thin wrappers around it, and external apps can use it
directly instead of speaking raw HTTP:

```python
from lib import MermaidClient

client = MermaidClient()          # host/port from MERMAID_HOST/MERMAID_PORT (.env)
client.start_server()             # start the daemon if it isn't running

client.add_task("Add a BuildScript for lz4", priority=10)
print(client.health())
for task in client.list_tasks():
    print(task["id"], task["status"])

# custom tools, without writing boilerplate
schema = {"type": "function", "function": {"name": "myapp_lookup", ...}}
client.register_tool(schema, "http://127.0.0.1:9000/tools/lookup",
                     agents=["developer"])
client.unregister_tool("myapp_lookup")
client.shutdown_server()
```

Every method raises `MermaidServerError` on HTTP errors or when the server
is unreachable, so callers only need to handle one exception type.

### HTTP tool API (for any app, any language)

The Python helper above is a convenience wrapper — the underlying extension
point is the server's HTTP API, so **any application** (Node, Rust, Go, a
GUI, another service…) can add tools on top of the built-in ones. The app
hosts an HTTP endpoint (localhost) that receives tool arguments and returns
a JSON result, then registers itself with the server:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST /tools` | `{schema, callback, agents?, replace?}` | register a custom tool |
| `PUT /tools/<name>` | `{schema, callback, agents?}` | register or replace (upsert, hot-reload friendly) |
| `GET /tools` | `?builtin=true` to include built-ins | list registered tools |
| `GET /tools/<name>` | — | full description (schema, agents, callback) |
| `DELETE /tools/<name>` | — | remove a custom tool (built-ins are protected) |
| `POST /tools/<name>` | `{args}` | invoke a tool directly (also used for callbacks) |

The `schema` is an OpenAI-style function schema; the `callback` is the
localhost URL the server POSTs `{"tool": name, "args": {...}}` to when the
agent calls the tool. The callback's JSON response is fed back to the agent
as the tool result (return `{"result": ...}` or `{"error": "..."}`).

Minimal example with curl:

```bash
# register a tool backed by your app's endpoint on port 9000
curl -X PUT http://127.0.0.1:7331/tools/myapp_lookup \
  -H 'Content-Type: application/json' \
  -d '{
    "schema": {"type": "function", "function": {
      "name": "myapp_lookup",
      "description": "Look up a record in my app.",
      "parameters": {"type": "object", "properties": {"q": {"type": "string"}},
                     "required": ["q"]}}},
    "callback": "http://127.0.0.1:9000/tools/lookup",
    "agents": ["developer"]
  }'

# inspect / remove
curl http://127.0.0.1:7331/tools
curl -X DELETE http://127.0.0.1:7331/tools/myapp_lookup
```

Or with the CLI:

```bash
mermaid tool list          # custom tools
mermaid tool list --all    # include built-ins
mermaid tool show myapp_lookup
mermaid tool remove myapp_lookup
```

Custom tools compose with the built-ins: an external app can register a
tool whose handler itself calls `POST /tools/<builtin>` (e.g. `bash` or
`read_file`) to build higher-level domain tools on top of the agent's
existing capabilities.

The SplashOS-specific tools live in the
[splashos_agent_enviroment](https://github.com/MVDW-SplashOS/splashos_agent_enviroment)
repository (`mermaid-tools/`).

## Configuration

Everything comes from environment variables; `.env` (repo root or
`/etc/mermaid/.env`) is the only config file — no YAML task files.

| Env var | Default |
| --- | --- |
| `MERMAID_PROVIDER` | `llamacpp` — also `openai` (any OpenAI-compatible server) or `ollama` |
| `MERMAID_PROVIDER_URL` | `http://localhost:8080` |
| `MERMAID_MODEL` | `qwen3.5:27b-q4_K_M` |
| `MERMAID_NUM_CTX` | `98304` |
| `MERMAID_API_KEY` | *(unset)* — bearer token for the provider |
| `MERMAID_DB_PATH` | `./mermaid.db` |
| `MERMAID_ENVIRONMENT_ROOT` | `~/Documents/GitHub/splashos_agent_enviroment` — the working directory agents operate on |
| `MERMAID_HOST` | `127.0.0.1` |
| `MERMAID_PORT` | `7331` |
| `MERMAID_SAFETY_AGENT` | `false` |
| `MERMAID_DEBUG` | `false` |

Tasks are queued through the client (`mermaid task add`) and stored in the
database, ordered by priority (lower runs first).

## Safety

Because the agent is not containerized, the optional safety agent is the main
guard: it denies destructive commands (`rm -rf /`, `dd` to block devices,
fork bombs, `curl | bash`, unsandboxed `sudo`, etc.) and approves low-risk
read/build commands. Enable it with `MERMAID_SAFETY_AGENT=true` and review
`mermaid/prompts/system/safety.md` before running.
