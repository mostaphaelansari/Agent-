# Architecture — `multi-agent-local`

> A locally-runnable, AgentCore-compatible multi-agent chatbot system built with **Strands Agents** and **AWS Bedrock**, with local **OpenTelemetry / Jaeger** observability.

---

## 1. Overview

`multi-agent-local` is a three-tier, hierarchical multi-agent system that gives a conversational chatbot the ability to autonomously browse the web. It runs interactively on a developer workstation and exposes a Bedrock AgentCore-compatible HTTP entrypoint, so it can be invoked both as a local REPL and as an AgentCore runtime.

```
User → Chatbot Agent → Orchestrator Agent → Browser Agent → Web
```

All AI inference is powered by **AWS Bedrock** (`us.anthropic.claude-sonnet-4-6`), accessed through the **Strands Agents** framework. Conversation history is persisted locally in **SQLite**. Traces are exported via OTLP to a local **Jaeger** instance.

---

## 2. Repository Layout

```
multi-agent-local/
├── app.py                          # Local REPL entry point
├── agent_runtime.py                # AgentCore-compatible HTTP entrypoint + OTEL setup
├── memory.py                       # SQLite conversation memory
├── docker-compose.yml              # Local Jaeger (OTLP collector + UI)
├── requirements.txt                # Python dependencies
├── .env                            # Local env vars (not committed)
├── agents/
│   ├── __init__.py
│   ├── chatbot_agent.py            # Tier 1 — Conversational front-end
│   ├── orchestrator_agent.py       # Tier 2 — Task decomposition
│   └── browser_agent.py            # Tier 3 — Web automation specialist
├── screenshots/                    # Browser session screenshots (runtime)
└── memory.db                       # SQLite DB (runtime, gitignored)
```

---

## 3. Agent Hierarchy

### 3.1 Chatbot Agent ([agents/chatbot_agent.py](agents/chatbot_agent.py))

| Property | Value |
|---|---|
| **Role** | User-facing conversational interface |
| **Model** | `BedrockModel` via `BEDROCK_MODEL_ID` env var |
| **Tool** | `delegate(task: str)` |
| **Backed by** | `strands.Agent` |

The chatbot agent handles the full conversation lifecycle:
1. Loads the last 10 turns from SQLite and builds a conversation context string.
2. Sends the context to the Bedrock LLM.
3. If the user intent requires web interaction, the LLM calls the `delegate` tool, which forwards the request to the Orchestrator Agent.
4. Saves both the user message and the assistant reply to SQLite.

```python
@tool
def delegate(task: str) -> str:
    """Send an action task (web lookup, scraping, form fill) to the orchestrator."""
    return run_orchestration(task)
```

### 3.2 Orchestrator Agent ([agents/orchestrator_agent.py](agents/orchestrator_agent.py))

| Property | Value |
|---|---|
| **Role** | Task decomposer and specialist dispatcher |
| **Model** | `BedrockModel` via `BEDROCK_MODEL_ID` env var |
| **Tool** | `call_browser_agent(goal: str)` |
| **Backed by** | `strands.Agent` |

The orchestrator receives a high-level task string from the chatbot, breaks it down if necessary, and delegates execution to the appropriate specialist. Currently the only registered specialist is the Browser Agent.

```python
@tool
def call_browser_agent(goal: str) -> dict:
    """Delegate a web-browsing goal to the Browser Agent."""
    return run_browser_task(goal)
```

### 3.3 Browser Agent ([agents/browser_agent.py](agents/browser_agent.py))

| Property | Value |
|---|---|
| **Role** | Web browsing and scraping specialist |
| **Model** | `BedrockModel` via `BEDROCK_MODEL_ID` env var |
| **Tool** | `AgentCoreBrowser.browser` (from `strands_tools`) |
| **Backed by** | `strands.Agent` |

The browser agent uses the `AgentCoreBrowser` tool from `strands-agents-tools`, which wraps Playwright to navigate the web. It is instructed to return structured JSON: `{result, steps, session_url}`.

```python
_browser_tool = AgentCoreBrowser(region=_REGION)

browser_agent = Agent(
    model=BedrockModel(...),
    tools=[_browser_tool.browser],
    system_prompt="... return STRICT JSON: {result, steps, session_url}.",
)
```

---

## 4. Data Flow

```
┌──────────┐  user message   ┌────────────────┐
│  User /  │ ──────────────► │ Chatbot Agent  │
│  Client  │                 │ (strands.Agent)│
└──────────┘                 └───────┬────────┘
      ▲                              │ delegate(task)        ┌──────────────┐
      │                              ▼                       │   SQLite     │
      │                    ┌──────────────────────┐          │  memory.db   │
      │                    │ Orchestrator Agent   │◄────────►│  (turns)     │
      │                    │   (strands.Agent)    │          └──────────────┘
      │ final reply        └──────────┬───────────┘
      │                              │ call_browser_agent(goal)
      │                              ▼
      │                    ┌──────────────────────┐
      │                    │   Browser Agent      │
      │                    │   (strands.Agent)    │
      │                    └──────────┬───────────┘
      │                              │ AgentCoreBrowser.browser(...)
      │                              ▼
      │                         ┌─────────┐
      └─────────────────────────│   Web   │
                                └─────────┘

       All three agents emit OTLP traces ──► Jaeger (localhost:4318)
```

All three agents make independent calls to **AWS Bedrock** for LLM inference. Only the Chatbot Agent reads/writes to the memory store. Spans from all three tiers are exported via OTLP to the local Jaeger collector.

---

## 5. Memory & State

| Layer | Technology | Scope |
|---|---|---|
| Short-term conversation | SQLite (`memory.db`) | Per `session_id`, last 10 turns |
| Browser state | Playwright in-process | Per `run_browser_task` call |

The local SQLite store ([memory.py](memory.py)) exposes two functions:
- `save_turn(session_id, role, text)` — appends a turn.
- `get_last_turns(session_id, k=10)` — retrieves the last `k` turns ordered by time.

The `session_id` is hard-coded to `"local-session-1"` in `app.py`; under `agent_runtime.py` it is taken from the AgentCore request context (`context.session_id`) with `"default"` as a fallback.

---

## 6. Entry Points

### 6.1 Local REPL ([app.py](app.py))

Runs an interactive `input()` loop, hard-coding `session_id = "local-session-1"`. Suitable for local development and quick chat tests.

```bash
python app.py
```

### 6.2 AgentCore-compatible HTTP runtime ([agent_runtime.py](agent_runtime.py))

Wraps the same `chat()` function in a `BedrockAgentCoreApp`. The `@app.entrypoint` handler accepts a payload with a `prompt` or `message` key and returns `{"result": <reply>}`. On import, it also wires up OpenTelemetry via `StrandsTelemetry().setup_otlp_exporter()` so traces flow to the OTLP endpoint configured by env vars (default `http://localhost:4318`).

```bash
python agent_runtime.py        # starts the BedrockAgentCoreApp HTTP server
```

---

## 7. Observability

Local tracing is provided by **Jaeger** running in Docker, with traces produced by Strands Agents through the OpenTelemetry SDK.

### 7.1 Pipeline

```
Strands Agents ─► OpenTelemetry SDK ─► OTLP/HTTP (4318) ─► Jaeger all-in-one ─► UI (16686)
```

### 7.2 Setup ([docker-compose.yml](docker-compose.yml))

```yaml
services:
  jaeger:
    image: jaegertracing/all-in-one:1
    ports:
      - "16686:16686"   # Jaeger UI
      - "4318:4318"     # OTLP HTTP
      - "4317:4317"     # OTLP gRPC
    environment:
      - COLLECTOR_OTLP_ENABLED=true
```

Start it with:

```bash
docker compose up -d
```

Then open the Jaeger UI at `http://localhost:16686` and filter by service `multi-agent-local`.

### 7.3 OTEL configuration

[agent_runtime.py](agent_runtime.py) sets sane defaults that can be overridden via environment:

| Variable | Default | Purpose |
|---|---|---|
| `OTEL_SERVICE_NAME` | `multi-agent-local` | Service name shown in Jaeger |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | OTLP HTTP collector |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` | Wire format |

Note: `app.py` does **not** initialize OTEL, so the local REPL is not traced. To trace REPL runs, prefer `agent_runtime.py`.

---

## 8. Configuration

### 8.1 Environment Variables ([.env](.env))

| Variable | Example Value | Purpose |
|---|---|---|
| `AWS_REGION` | `us-west-2` | AWS region for all Bedrock API calls |
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-sonnet-4-6` | Model used by all three agents |

AWS credentials are resolved by the standard boto3 chain (env vars, `~/.aws/credentials`, SSO, IAM role).

---

## 9. Dependencies ([requirements.txt](requirements.txt))

| Package | Role |
|---|---|
| `strands-agents` | Core `Agent` + `@tool` abstraction (Strands framework) |
| `strands-agents-tools` | `AgentCoreBrowser` Playwright-backed browser tool |
| `bedrock-agentcore` | `BedrockAgentCoreApp` HTTP runtime |
| `boto3` | AWS SDK (Bedrock API calls) |
| `playwright` | Headless browser automation (used by `strands_tools`) |
| `nest-asyncio` | Allows nested event loops (required for Playwright in sync context) |
| `python-dotenv` | Loads `.env` file at startup |
| `opentelemetry-exporter-otlp-proto-http` | OTLP/HTTP exporter for traces |

---

## 10. Known Limitations & Future Work

| Area | Current State | Future Direction |
|---|---|---|
| Memory | Local SQLite; context passed as a raw string | Replace with AgentCore cloud memory SDK for cross-session persistence |
| Browser tool | Single `AgentCoreBrowser` instance per process | Support MCP servers for pluggable tool backends |
| Orchestrator specialists | Only Browser Agent registered | Add more specialist agents (e.g., data analyst, form-filler) as additional `@tool` entries |
| Authentication | IAM / SSO profile on workstation | AgentCore execution role when deployed |
| Observability | OTEL wired in `agent_runtime.py` only | Initialize OTEL in `app.py` for trace parity between REPL and runtime |
| Session management | Hard-coded session in `app.py`; `context.session_id` in runtime | Dynamic session IDs for multi-user support |
| Deployment | Local only (REPL + AgentCore HTTP runtime) | Re-introduce AgentCore deployment config (Dockerfile, ECR, `.bedrock_agentcore.yaml`) |
