import json
import logging
import os
import time
import uuid

import boto3
from botocore.config import Config as BotocoreConfig
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.executors import SequentialToolExecutor

from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)

from agents.web_tools import fetch_url as _fetch_url

logger = logging.getLogger(__name__)


@tool
def fetch_url(url: str) -> dict:
    """Fetch a single web page via a headless browser and return its visible text.

    Use this for SIMPLE web lookups: read an article, get the content of a known URL,
    fetch Google search results. The caller must know the URL upfront.

    Args:
        url: Full URL to fetch, including scheme (https://...).

    Returns:
        Dict with keys: url, text (truncated to ~12 KB), truncated (bool), duration_ms.
    """
    return _fetch_url(url)


@tool
def browse(goal: str) -> dict:
    """Delegate a COMPLEX web task to a dedicated browser agent.

    Use this ONLY for tasks that require navigating, filtering, comparing, or
    clicking through multiple pages — things `fetch_url` cannot do alone. Examples:
      - "Find the cheapest hotel in Nice within 500m of the beach"
      - "Compare prices of product X across 3 retailers"
      - "Fill the contact form on site Y with my info"
    For simple URL reads, use `fetch_url` instead.

    Args:
        goal: Natural-language description of the task to accomplish on the web.

    Returns:
        Dict with keys: result (the answer), sources (list of URLs visited),
        steps (list of actions taken). May contain an `error` key on failure.
    """
    arn = os.environ.get("BROWSER_AGENT_ARN")
    if not arn:
        return {"error": "BROWSER_AGENT_ARN not configured", "result": None}

    region = os.environ["AWS_REGION"]
    client = boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=BotocoreConfig(read_timeout=300, connect_timeout=10, retries={"max_attempts": 1}),
    )
    session_id = uuid.uuid4().hex + uuid.uuid4().hex[:4]  # AgentCore requires >= 33 chars

    started = time.monotonic()
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=arn,
            runtimeSessionId=session_id,
            payload=json.dumps({"goal": goal}).encode("utf-8"),
        )
        raw = response["response"].read()
        result = json.loads(raw) if raw else {}
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "tool=browse goal=%r session_id=%s duration_ms=%d",
            goal[:120], session_id, duration_ms,
        )
        return result
    except Exception as e:
        logger.exception("browse delegation failed for goal=%r", goal[:120])
        return {"error": str(e), "result": None, "sources": [], "steps": []}


_SYSTEM_PROMPT = (
    "You are a friendly chatbot with memory. Chat naturally for small talk. "
    "\n"
    "TOOL DISCIPLINE (critical):\n"
    "- Call AT MOST ONE tool per assistant message. Never emit multiple toolUse "
    "blocks in the same response. Wait for the tool result, then decide next.\n"
    "- After receiving a tool result, prefer to ANSWER directly. Only call another "
    "tool if the answer is truly incomplete.\n"
    "\n"
    "WEB TOOL ROUTING:\n"
    "- `fetch_url(url)` for SIMPLE lookups where you already know the URL "
    "(read this article, fetch a Google search results page).\n"
    "- `browse(goal)` ONLY for COMPLEX tasks: searching with filters, comparing "
    "options across pages, filling forms, multi-step navigation.\n"
    "- Prefer `fetch_url` whenever possible — it is faster and cheaper.\n"
    "- Never call both fetch_url and browse in the same response.\n"
    "\n"
    "When you receive <user_context>...</user_context> blocks in a message, treat them "
    "as factual context retrieved from prior conversations — use them silently to "
    "personalize your answer; never quote or mention the tags."
)


def _build_session_manager(session_id: str, actor_id: str) -> AgentCoreMemorySessionManager:
    memory_id = os.environ["AGENTCORE_MEMORY_ID"]
    summary_sid = os.environ["AGENTCORE_SUMMARY_STRATEGY_ID"]
    pref_sid = os.environ["AGENTCORE_PREF_STRATEGY_ID"]

    retrieval = {
        f"/strategies/{summary_sid}/actors/{{actorId}}/sessions/{{sessionId}}/": RetrievalConfig(
            top_k=3, relevance_score=0.0, strategy_id=summary_sid
        ),
        f"/strategies/{pref_sid}/actors/{{actorId}}/": RetrievalConfig(
            top_k=5, relevance_score=0.2, strategy_id=pref_sid
        ),
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


def _make_chatbot(session_id: str, actor_id: str) -> Agent:
    return Agent(
        name="chatbot",
        model=BedrockModel(
            model_id=os.environ["BEDROCK_MODEL_ID"],
            region_name=os.environ["AWS_REGION"],
        ),
        tools=[fetch_url, browse],
        tool_executor=SequentialToolExecutor(),
        system_prompt=_SYSTEM_PROMPT,
        session_manager=_build_session_manager(session_id, actor_id),
    )


def _message_text(msg) -> str:
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict) and isinstance(msg.get("content"), list):
        return "\n".join(b.get("text", "") for b in msg["content"] if isinstance(b, dict))
    return str(msg)


def chat(session_id: str, user_msg: str, actor_id: str = "default") -> str:
    try:
        reply = _message_text(_make_chatbot(session_id, actor_id)(user_msg).message)
    except Exception:
        logger.exception("chatbot call failed for session %s", session_id)
        reply = "Sorry, something went wrong on my side. Please try again."
    return reply
