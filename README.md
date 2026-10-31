# merMAID

A Python multi-agent coding agent for the [SplashOS](https://github.com/MVDW-Java/SplashOS)
agent environment. The architecture is modeled on
[astra](https://github.com/MVDW-Java/astra) (planner / developer / tester /
reviewer agents with tool calling), rewritten in Python and specialized for
the SplashOS working environment (`~/Documents/GitHub/splashos_agent_enviroment`).

It runs **directly on the host** — no container, no sandbox — with full
global access to the filesystem and shell.

## Architecture

**Server / client split.** The server is a background daemon that owns the
agent loop; the client is a thin CLI that talks to it over a local HTTP API.
You can close the terminal — the server keeps working.

```
client (CLI) ──HTTP──▶ server (daemon, 127.0.0.1:7331)
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
```

Run the module directly if you don't install a wrapper script:

```bash
python3 -m mermaid start
python3 -m mermaid.server   # run the server in the foreground
```

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
| `MERMAID_ENVIRONMENT_ROOT` | `~/Documents/GitHub/splashos_agent_enviroment` |
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
