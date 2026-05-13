# Memory in `multi-agent-local`

This document explains how the chatbot remembers things across messages and across
sessions. Only the `multi_agent_chatbot` runtime uses memory; the `browser_agent`
sub-agent is stateless (`memory.mode: NO_MEMORY` in `.bedrock_agentcore.yaml`).

## What "memory" means here

The project uses **AgentCore Memory** (`STM_AND_LTM` mode) — a managed service that
stores conversation events and runs background "strategies" that distill those
events into searchable knowledge.

Two layers:

- **Short-term memory (STM)** — the raw turn-by-turn event log for the current
  session. Re-hydrated automatically when the same `session_id` reconnects, so
  the bot doesn't forget what was said two messages ago.
- **Long-term memory (LTM)** — strategy outputs derived from the event log:
  - **Summaries** of past conversations (one strategy)
  - **User preferences** extracted across all sessions (another strategy)

Both strategies are queried at the start of every turn and the matching results
are injected into the model's context before it answers.

## The memory resource

Provisioned once per environment by `scripts/provision_memory.py`:

```
MEMORY_NAME    = "multi_agent_chatbot_memory"
REGION         = eu-west-1
EVENT_EXPIRY   = 30 days
STRATEGIES     = [summaryMemoryStrategy, userPreferenceMemoryStrategy]
```

After creation the IDs are written into `.env`:

```
AGENTCORE_MEMORY_ID=multi_agent_chatbot_memory-eQ2WgV4ihg
AGENTCORE_SUMMARY_STRATEGY_ID=ConversationSummary-EXmMY36xn1
AGENTCORE_PREF_STRATEGY_ID=UserPreferences-5pYJRbG7Rn
```

The same IDs are recorded in `.bedrock_agentcore.yaml` under
`agents.multi_agent_chatbot.memory`. Events older than 30 days are deleted
automatically by the service.

## Actors and sessions

Two identifiers scope every piece of memory:

| ID           | Source                                                    | Scope                          |
|--------------|-----------------------------------------------------------|--------------------------------|
| `session_id` | `context.session_id` in deployed runtime, CLI flag locally | One conversation               |
| `actor_id`   | `payload["actor_id"]` or `--actor` flag (default `"default"`) | One end user across sessions |

- STM is keyed by `(actor_id, session_id)` — a new `session_id` starts a fresh
  conversation but keeps access to the actor's long-term memory.
- The user-preferences strategy is keyed by `actor_id` only — so preferences
  learned in session A are available in session B for the same actor.

Locally, `app.py` auto-generates `local-<hex>` session IDs unless `--session` is
passed. In the deployed runtime, `agent_runtime.py` reads
`context.session_id` and falls back to the literal string `"default"` if absent
(meaning sessionless invocations all share one bucket — fine for testing, not
for production).

## Wiring the chatbot to memory

The integration point is `_build_session_manager()` in `agents/chatbot.py`:

```python
retrieval = {
    f"/strategies/{summary_sid}/actors/{{actorId}}/sessions/{{sessionId}}/":
        RetrievalConfig(top_k=3, relevance_score=0.0, strategy_id=summary_sid),
    f"/strategies/{pref_sid}/actors/{{actorId}}/":
        RetrievalConfig(top_k=5, relevance_score=0.2, strategy_id=pref_sid),
}

return AgentCoreMemorySessionManager(
    agentcore_memory_config=AgentCoreMemoryConfig(
        memory_id=memory_id,
        session_id=session_id,
        actor_id=actor_id,
        retrieval_config=retrieval,
        filter_restored_tool_context=True,
    ),
    region_name=os.environ["AWS_REGION"],
)
```

Pieces worth understanding:

- **Retrieval paths** are templates. `{actorId}` and `{sessionId}` are filled in
  by the session manager at call time. The summary path is actor + session
  scoped; the preferences path is actor scoped only.
- **`top_k`** caps the number of memory records returned per strategy:
  3 summaries, 5 preferences.
- **`relevance_score`** is a minimum similarity threshold. The summary strategy
  uses `0.0` (return the best 3 regardless of relevance) and preferences use
  `0.2` (skip weak matches). The summary threshold is intentionally permissive
  but means stale summaries can leak in for long-lived actors — worth tuning if
  context bloat becomes a problem.
- **`filter_restored_tool_context=True`** strips replayed tool-call blocks from
  rehydrated STM. Without this, every restart would re-inject historical tool
  output into the model's context.

The session manager is passed to the Strands `Agent` in `_make_chatbot()`. From
that point on, Strands handles persistence transparently: every user message and
assistant reply is written back to the event log, and every new turn pulls
matching records from both strategies.

## How retrieved memory reaches the model

Retrieved records arrive as `<user_context>...</user_context>` blocks prepended
to the user's message. The system prompt instructs the model how to treat them
(`agents/chatbot.py`):

```
When you receive <user_context>...</user_context> blocks in a message, treat them
as factual context retrieved from prior conversations — use them silently to
personalize your answer; never quote or mention the tags.
```

So the model sees something like:

```
<user_context>
The user prefers concise answers and works in Python.
</user_context>
what's the cleanest way to deduplicate a list?
```

and responds in a way that reflects the preference without echoing the tag.

## Event lifecycle

1. `chat(session_id, user_msg, actor_id)` is called.
2. `AgentCoreMemorySessionManager` looks up STM for `(actor_id, session_id)`
   and rehydrates the conversation into the Strands agent.
3. It also fires retrieval queries against both strategy paths and prepends
   the results as `<user_context>` blocks.
4. The model generates a reply, possibly calling tools (`fetch_url`, `browse`).
5. The new user turn and the assistant turn are appended to the event log.
6. Asynchronously, AgentCore's background strategy workers re-run summarisation
   and preference extraction over recent events — so future turns benefit from
   what just happened, but not within the same turn.

## Inspecting memory

`scripts/view_memory.py` lists the stored preference nodes for the default
actor:

```bash
python scripts/view_memory.py
```

It hits `client.list_nodes(memory_id, path=/strategies/{pref_id}/actors/default/)`
and prints each payload. The actor is hardcoded to `"default"` — change the
script (or parameterise it) to inspect another actor.

To inspect raw events or summaries you'd call `MemoryClient.list_events()` /
`list_nodes()` with the summary strategy path; that's not wrapped by a script
yet.

## Why this design

- **STM + LTM split** lets the bot scale to long histories without paying the
  cost of replaying every past message — old turns get distilled to summaries
  and preferences, and only the relevant distillates are pulled into context.
- **Actor-scoped preferences + session-scoped summaries** mean that switching
  to a new session keeps personalisation (preferences) while starting the
  conversational thread fresh (no stale summaries from unrelated chats).
- **Managed strategies** push the extraction work onto AgentCore, so the
  chatbot code stays small — provisioning is ~50 lines, integration is one
  `SessionManager` construction.

## What is *not* in memory

- The `browser_agent` runtime — every `browse` call gets a fresh agent and a
  fresh browser session, by design (`memory.mode: NO_MEMORY`).
- Tool outputs from past sessions — `filter_restored_tool_context=True` strips
  them on rehydration, so the model doesn't re-read old `fetch_url` text.
- Anything older than 30 days — `event_expiry_days=30` is the hard ceiling.
