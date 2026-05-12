# Architecture — `multi-agent-local`

> A multi-agent chatbot built with **Strands Agents**, **AWS Bedrock**, and the full **Amazon Bedrock AgentCore** stack (Runtime + Browser + Memory + Observability). Two independent runtimes deployed in `eu-west-1`.

---

## 1. Overview

The system is composed of **two independent AgentCore Runtimes** that communicate over HTTP:

```
User → Runtime #1 (chatbot)  ──┬─ fetch_url(url)   ── direct Browser API call
                               │
                               └─ browse(goal) ────► Runtime #2 (browser_agent) ──► Browser API
```

- **Runtime #1 — `multi_agent_chatbot`**: the conversational front-end. Holds memory, decides whether a web task is simple (call `fetch_url` directly) or complex (delegate to `browse`).
- **Runtime #2 — `browser_agent`**: a dedicated, bounded sub-agent that drives the AgentCore Browser for multi-step navigation. Returns strict JSON.

Communication between the two runtimes is **stateless HTTP** via `bedrock-agentcore:InvokeAgentRuntime`. No in-process nesting of LLM event loops — this is the key architectural decision that makes the system observable and bounded (see §11).

All inference uses **AWS Bedrock** with `eu.anthropic.claude-sonnet-4-6` (cross-region EU inference profile). Conversation history is persisted in **AgentCore Memory**. Traces go to **CloudWatch + X-Ray** via OpenTelemetry auto-instrumentation.

---

## 2. Repository Layout

```
multi-agent-local/
├── app.py                          # Local REPL (argparse + UUID session + actor_id)
├── agent_runtime.py                # Runtime #1 entrypoint (chatbot)
├── telemetry_setup.py              # Opt-in local OTEL (ENABLE_LOCAL_OTEL=1)
├── requirements.txt                # Python dependencies
├── .env                            # Local env vars (gitignored)
├── .bedrock_agentcore.yaml         # Deployment config — gitignored (ARNs, account)
│
├── agents/
│   ├── __init__.py
│   ├── chatbot.py                  # Strands Agent (the LLM) + fetch_url + browse tools
│   └── web_tools.py                # Deterministic fetch_url() Python function
│
├── browser_agent/                  # Runtime #2 — separate AgentCore deployable
│   ├── __init__.py
│   └── agent_runtime.py            # Self-contained: bounded Strands Agent + entrypoint
│
├── scripts/
│   └── provision_memory.py         # One-shot: create AgentCore Memory resource + strategies
│
├── plan_implementation.md          # Roadmap (phased plan)
└── architecture.md                 # This file
```

---

## 3. Agents

### 3.1 Chatbot — `agents/chatbot.py` (Runtime #1)

| Property | Value |
|---|---|
| **Role** | User-facing LLM. Owns memory. Routes web tasks between two tools. |
| **Model** | `BedrockModel` (`BEDROCK_MODEL_ID`) |
| **Tools** | `fetch_url(url)`, `browse(goal)` |
| **Session manager** | `AgentCoreMemorySessionManager` (STM + LTM injection) |
| **Tool executor** | `SequentialToolExecutor` (serializes parallel tool intents from the LLM) |
| **Span name** | `invoke_agent chatbot` |

The chatbot's `chat(session_id, user_msg, actor_id)` flow:

1. Build a fresh `AgentCoreMemorySessionManager` for this `(session_id, actor_id)` pair. Cannot be cached — different sessions need different managers.
2. Strands' session manager hooks load prior session events (STM) and inject retrieved `<user_context>` blocks (LTM) before the LLM call.
3. The LLM responds. If it emits one or more `toolUse` blocks, `SequentialToolExecutor` runs them one at a time.
4. After tool results, the LLM produces the final assistant message.
5. The session manager auto-persists user and assistant messages as AgentCore Memory events.

### 3.2 Browser sub-agent — `browser_agent/agent_runtime.py` (Runtime #2)

| Property | Value |
|---|---|
| **Role** | Web browsing specialist. Multi-step navigation, filtering, comparison. |
| **Model** | Same as chatbot (`BedrockModel`) |
| **Tool** | `AgentCoreBrowser.browser` (from `strands_tools.browser`) |
| **Tool executor** | `SequentialToolExecutor` |
| **Hook** | `ToolCallCap(max_calls=6)` — `BeforeToolCallEvent` cancels further tool calls after 6 |
| **Span name** | `invoke_agent browser` |
| **Output** | Strict JSON: `{result, sources, steps}` |

The agent is rebuilt fresh per invocation (no `lru_cache`) so the `ToolCallCap` counter resets between requests. The system prompt enforces:
- One `init_session` only, reuse the same `session_name` for subsequent actions.
- Specific CSS selectors for `get_text` (no full `<body>`).
- Hard limit: 6 tool calls.
- JSON-only output (code fences are stripped server-side before parsing).

---

## 4. Data Flow

```
                  ┌────────────────────────────────────────────────────┐
                  │              Runtime #1 — chatbot                  │
   user prompt    │  ┌──────────────────────────────────────────────┐  │
       ─────────────▶│ Strands Agent (LLM)                          │  │
                  │  │   ├─ tool fetch_url(url)  ─── boto3 ───────┐ │  │
                  │  │   ├─ tool browse(goal)   ─── boto3 ──┐    │ │  │
                  │  │   └─ session_manager: AgentCore Memory│    │ │  │
                  │  └──────────────────────────────────────┘    │ │  │
                  └─────────────────────────────────────────│────│─┘  │
                                                            │    │
   ┌─────────────────────────────────────────────────┐      │    │
   │  Runtime #2 — browser_agent                     │◄─────┘    │
   │  ┌────────────────────────────────────────┐    │            │
   │  │ Strands Agent (LLM) + ToolCallCap=6    │    │            │
   │  │   └─ tool browser  ──┐                 │    │            │
   │  └──────────────────────┼─────────────────┘    │            │
   └─────────────────────────│──────────────────────┘            │
                             ▼                                   ▼
                  ┌──────────────────────────────────────────────────┐
                  │  AgentCore Browser  (managed Chrome over CDP)    │
                  └──────────────────────────────────────────────────┘

   OTEL spans ──► CloudWatch Logs + X-Ray  (per runtime, separate trees)
   Memory events ──► AgentCore Memory (STM events + LTM strategies)
```

`fetch_url` and `browse` both ultimately use the **same AgentCore Browser service** — the difference is **who drives it**:

- `fetch_url`: deterministic Python (open → navigate → extract → close). One tool call per URL. ~5-10s.
- `browse`: another LLM (the browser sub-agent) makes step-by-step decisions. ~30-100s for a real task. Capped at 6 actions.

---

## 5. Memory & State

### 5.1 AgentCore Memory

| Strategy | Scope | Triggered by | Used for |
|---|---|---|---|
| `ConversationSummary` (built-in `SUMMARY`) | Per `session_id` | Async after session events | Long-running session compression |
| `UserPreferences` (built-in `USER_PREFERENCE`) | Per `actor_id`, cross-session | Async (~5-10 min lag) | Persistent user traits across conversations |

The session manager queries both strategies' namespaces on each new user message and injects retrieved records as `<user_context>...</user_context>` blocks prepended to the user's content. The system prompt instructs the LLM to use these silently.

**Critical config**: `filter_restored_tool_context=True` strips historical `toolUse`/`toolResult` blocks from restored messages. Without this, partial tool exchanges from prior turns can desynchronize the Bedrock Converse API contract, raising `ValidationException`.

### 5.2 Session and actor identity

| ID | Source | Effect |
|---|---|---|
| `session_id` | AgentCore `context.session_id` (≥33 chars) | Isolates conversation state per session |
| `actor_id` | Payload `actor_id`, defaults to `"default"` | Aggregates `UserPreferences` across sessions for the same user |

For multi-user deployments, set `actor_id` to a stable user identifier (e.g., from an auth layer) in the payload.

### 5.3 No SQLite

The earlier `memory.py` SQLite implementation has been removed. AgentCore Memory replaces it with managed persistence and cross-session retrieval.

---

## 6. Entry Points

### 6.1 Local REPL — `app.py`

```bash
python app.py                                  # auto session, actor=default
python app.py --session my-session             # explicit session
python app.py --actor mostapha                 # explicit actor_id for LTM
python app.py --log-level DEBUG                # verbose logs
```

`load_dotenv()` is called at startup so local AWS / Bedrock / Memory env vars are picked up from `.env`.

### 6.2 Runtime #1 — `agent_runtime.py`

The chatbot's AgentCore entrypoint. Accepts payloads with `prompt` (or `message`) and optional `actor_id`. Returns `{"result": <reply>}`.

### 6.3 Runtime #2 — `browser_agent/agent_runtime.py`

The browser sub-agent's entrypoint. Accepts payloads with `goal` (or `prompt`). Returns the agent's JSON output (parsed `{result, sources, steps}`).

Invocation is **only** from Runtime #1's `browse` tool. Direct invocation is supported for testing but not part of the user-facing API.

---

## 7. Observability

OpenTelemetry remains the unified tracing layer. With two runtimes deployed, each emits **its own X-Ray trace tree**, identified by the per-runtime service name. The chatbot's trace contains the `execute_tool browse` span; the browser_agent's trace contains the actual browser actions. Linking them requires correlating timestamps + session IDs (no auto-propagation across the HTTP boundary by default).

| Stream | Destination |
|---|---|
| Chatbot runtime logs | `/aws/bedrock-agentcore/runtimes/multi_agent_chatbot-C7Pn2Z94Bk-DEFAULT` |
| Browser_agent runtime logs | `/aws/bedrock-agentcore/runtimes/browser_agent-lgooiLHlvk-DEFAULT` |
| Local Jaeger (opt-in) | `ENABLE_LOCAL_OTEL=1` → `http://localhost:4318` |

The **GenAI Observability Dashboard** (`console.aws.amazon.com/cloudwatch/...#gen-ai-observability/agent-core`) aggregates both runtimes — useful for end-to-end latency breakdown.

---

## 8. Configuration

### 8.1 Environment Variables

| Variable | Where | Purpose |
|---|---|---|
| `AWS_REGION` | both runtimes + local | AWS region for Bedrock + AgentCore |
| `BEDROCK_MODEL_ID` | both runtimes + local | Bedrock model / inference profile |
| `AGENTCORE_MEMORY_ID` | chatbot + local | AgentCore Memory resource ID |
| `AGENTCORE_SUMMARY_STRATEGY_ID` | chatbot + local | SUMMARY strategy ID for retrieval |
| `AGENTCORE_PREF_STRATEGY_ID` | chatbot + local | USER_PREFERENCE strategy ID for retrieval |
| `BROWSER_AGENT_ARN` | chatbot only | Runtime ARN of the browser sub-agent |
| `ENABLE_LOCAL_OTEL` | local only | `1` to enable Jaeger export |
| `LOG_LEVEL` | both runtimes | Python logging level |

AWS credentials are resolved by the standard boto3 chain (env, `~/.aws/credentials`, SSO, IAM execution role in deployed runtimes).

### 8.2 IAM execution role

Both runtimes **share** the same execution role: `AmazonBedrockAgentCoreSDKRuntime-eu-west-1-1295f1482c`. Its inline policy includes:

| Sid | Why |
|---|---|
| `BedrockModelInvocation` | Call Bedrock with `InvokeModel*` + `CountTokens` |
| `BedrockAgentCoreCodeInterpreter` | Reserved for future `run_python` tool |
| `BedrockAgentCoreBrowser` | Start/Stop/Connect browser sessions |
| `BedrockAgentCoreMemory` | Read/write events on the memory resource |
| `InvokeBrowserAgent` | Runtime #1 → Runtime #2 HTTP delegation |
| `BedrockAgentCoreIdentity` + `*IdentityGet*` | Workload identity / OAuth (reserved) |
| ECR / Logs / X-Ray / Marketplace | Infra plumbing |

---

## 9. Dependencies

| Package | Role |
|---|---|
| `strands-agents` | `Agent`, `@tool`, `SequentialToolExecutor`, hooks |
| `strands-agents-tools` | `AgentCoreBrowser` for the browser sub-agent |
| `bedrock-agentcore` | `BedrockAgentCoreApp`, `AgentCoreMemorySessionManager`, `BrowserClient` |
| `boto3` | AWS SDK (Bedrock invoke, AgentCore HTTP) |
| `playwright` | Headless browser (transitive via `strands_tools` and `web_tools.fetch_url`) |
| `python-dotenv` | Load `.env` at startup |
| `opentelemetry-exporter-otlp-proto-http` | Local OTEL export |

In the deployed runtimes, `aws-opentelemetry-distro` is auto-injected by the AgentCore Dockerfile.

---

## 10. Deployment

### 10.1 Two runtimes, one yaml

`.bedrock_agentcore.yaml` (gitignored — contains account, ARNs, memory_id) declares both agents under `agents:`. Each has its own `agent_id`, ECR repo (chatbot only; browser_agent uses `direct_code_deploy`), and ARN.

| Runtime | Deployment type | ARN suffix |
|---|---|---|
| `multi_agent_chatbot` | `container` (ARM64 via CodeBuild) | `runtime/multi_agent_chatbot-C7Pn2Z94Bk` |
| `browser_agent` | `direct_code_deploy` (Python 3.12) | `runtime/browser_agent-lgooiLHlvk` |

### 10.2 Provisioning order (from scratch)

```bash
# 1. Create the Memory resource (one-shot)
AWS_PROFILE=default python scripts/provision_memory.py
# → prints AGENTCORE_MEMORY_ID and strategy IDs to add to .env

# 2. Deploy the browser sub-agent first (so we have its ARN)
AWS_PROFILE=default agentcore launch --agent browser_agent \
  --env AWS_REGION=eu-west-1 \
  --env BEDROCK_MODEL_ID=eu.anthropic.claude-sonnet-4-6

# 3. Deploy the chatbot with the browser_agent ARN + memory env vars
AWS_PROFILE=default agentcore launch --agent multi_agent_chatbot \
  --env AWS_REGION=eu-west-1 \
  --env BEDROCK_MODEL_ID=eu.anthropic.claude-sonnet-4-6 \
  --env AGENTCORE_MEMORY_ID=<id-from-step-1> \
  --env AGENTCORE_SUMMARY_STRATEGY_ID=<id-from-step-1> \
  --env AGENTCORE_PREF_STRATEGY_ID=<id-from-step-1> \
  --env BROWSER_AGENT_ARN=<arn-from-step-2>
```

**Note**: `agentcore launch --env` replaces all env vars on each deploy. Always pass the full set.

### 10.3 Operating commands

```bash
# Invoke the chatbot (use --cli-read-timeout for long browse calls)
AWS_PROFILE=power aws bedrock-agentcore invoke-agent-runtime \
  --agent-runtime-arn <chatbot-arn> \
  --runtime-session-id $(python -c 'import uuid; print(uuid.uuid4().hex*2)') \
  --payload "fileb:///path/to/payload.json" \
  --region eu-west-1 \
  --cli-read-timeout 600 \
  /tmp/response.json

# Tail logs
AWS_PROFILE=power aws logs tail \
  /aws/bedrock-agentcore/runtimes/multi_agent_chatbot-C7Pn2Z94Bk-DEFAULT \
  --since 10m --region eu-west-1
```

---

## 11. Design Decisions & Lessons Learned

### 11.1 Why two runtimes instead of nested Strands agents

An earlier revision had the browser agent as an in-process Strands sub-agent of the chatbot. Three failure modes emerged:

1. **Multiplicative event loops** — chatbot's N cycles × sub-agent's M cycles, both growing context.
2. **Sub-agent improvisation** — re-initialized browser sessions on every iteration (10× `init_session` for one task), inflating tokens.
3. **Chatbot redundancy** — called `browse` twice with near-identical goals when one invocation would suffice.

Result: 1.2M tokens, 95s for a single research task. The fix split the LLMs into separate runtimes communicating over stateless HTTP — no shared context, isolated event loops, separate observability. Token use dropped to ~25K (-98%) for the simple cases.

For complex tasks (e.g., hotel search), the cost is still meaningful (~25K-50K tokens, ~100s) because real multi-step browsing is intrinsically expensive. But it's now **bounded** (max 6 actions, hard cap) and **observable** (one trace per runtime).

### 11.2 Bounds and safeguards on the browser sub-agent

- **`SequentialToolExecutor`** — even if the LLM emits parallel tool intents, they execute one at a time.
- **`ToolCallCap` hook** — `BeforeToolCallEvent` cancels the 7th+ tool call with a directive to return final JSON now.
- **System prompt** — explicit "max 6 actions" + JSON-only output + reuse one browser session.
- **`boto3` read timeout** — 300s on `invoke_agent_runtime` from the chatbot's `browse` tool.

### 11.3 `filter_restored_tool_context`

AgentCore Memory's session manager restores prior conversation events on session start. By default it preserves historical `toolUse`/`toolResult` blocks. When these are restored mid-conversation, the Bedrock Converse API can see toolResult blocks without matching toolUse blocks from the immediately previous turn (because the previous turn's assistant message was reconstructed from event data that may have lost its tool count). Setting `filter_restored_tool_context=True` strips them, eliminating `ValidationException` from desynchronized history.

### 11.4 `fetch_url` vs `browse` — the router decision

Both tools live on the chatbot. The system prompt routes:
- Known URL → `fetch_url` (fast, deterministic, 5-10s).
- Search + compare + filter + navigate → `browse` (delegates to Runtime #2, slow but bounded).

The LLM is told to prefer `fetch_url` whenever possible. The split mirrors a fundamental property: **a tool is a function with a known signature; an agent is a goal-driven LLM**. We use a tool when the action sequence is predictable; an agent when it requires reasoning along the way.

---

## 12. Known Limitations & Future Work

| Area | Current State | Future Direction |
|---|---|---|
| **Streaming** | Synchronous response only | Stream LLM tokens to client → perceived latency drops dramatically on browse tasks |
| **Prompt injection** | `fetch_url` returns raw page text directly | Sandbox/escape web content before it reaches the LLM as context |
| **Test coverage** | None | `pytest` integration tests for chat / fetch / browse / memory recall |
| **Memory pruning** | `event_expiry_days=30` default | Tune per traffic, or implement consolidation strategies (semantic) |
| **Cross-runtime trace correlation** | Separate trace trees per runtime | Propagate W3C traceparent headers in the boto3 invocation |
| **Multi-user** | `actor_id="default"` everywhere | Wire to auth layer for real per-user preferences |
| **More specialists** | Just `browser_agent` | Add `code_agent` (CodeInterpreter), `data_agent` (Athena/DynamoDB), etc. — same Option B pattern (separate runtimes) |
| **Network mode** | PUBLIC | Switch to VPC mode for private endpoints |
| **CountTokens** | Was failing silently; IAM fixed | Cosmetic, but now succeeds |
