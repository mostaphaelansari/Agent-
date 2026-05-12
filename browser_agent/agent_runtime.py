import json
import logging
import os
from functools import lru_cache

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

import telemetry_setup  # noqa: F401

from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent
from strands.hooks import BeforeToolCallEvent
from strands.hooks.registry import HookRegistry
from strands.models import BedrockModel
from strands.tools.executors import SequentialToolExecutor
from strands_tools.browser import AgentCoreBrowser

logger = logging.getLogger(__name__)

_MAX_TOOL_CALLS = 6

_SYSTEM_PROMPT = (
    "You are a web browsing specialist. Given a GOAL, complete it and return "
    "ONLY valid JSON in this shape:\n"
    '{"result": "<concise answer>", "sources": ["<url1>", "<url2>"], "steps": ["<short step>", ...]}\n\n'
    "STRICT RULES:\n"
    "1. Call browser with action.type='init_session' EXACTLY ONCE at the start. "
    "Reuse the SAME session_name for ALL subsequent actions. NEVER re-init.\n"
    "2. Use specific CSS selectors for get_text (e.g. 'h1', '.price', '#main'). "
    "Never extract whole <body>.\n"
    "3. HARD LIMIT: maximum 6 tool calls. After your 6th tool call, you MUST emit "
    "the final JSON with whatever you have — partial results are acceptable.\n"
    "4. Output JSON ONLY. No prose, no markdown fences, no explanation outside the JSON object."
)


class ToolCallCap:
    """Hard-cap the number of tool calls inside a single agent invocation."""

    def __init__(self, max_calls: int):
        self.max_calls = max_calls
        self.count = 0

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self._on_before_tool)

    def _on_before_tool(self, event: BeforeToolCallEvent) -> None:
        self.count += 1
        if self.count > self.max_calls:
            event.cancel_tool = (
                f"Tool call cap reached ({self.max_calls}). Stop calling tools "
                "and emit your final JSON now with whatever partial results you have."
            )
            logger.warning(
                "tool_call_cap_hit count=%d max=%d tool=%s",
                self.count, self.max_calls, event.tool_use.get("name"),
            )


def _build_agent() -> Agent:
    region = os.environ["AWS_REGION"]
    browser_tool = AgentCoreBrowser(region=region)
    return Agent(
        name="browser",
        model=BedrockModel(
            model_id=os.environ["BEDROCK_MODEL_ID"],
            region_name=region,
        ),
        tools=[browser_tool.browser],
        tool_executor=SequentialToolExecutor(),
        system_prompt=_SYSTEM_PROMPT,
        hooks=[ToolCallCap(_MAX_TOOL_CALLS)],
    )


def _message_text(msg) -> str:
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict) and isinstance(msg.get("content"), list):
        return "\n".join(b.get("text", "") for b in msg["content"] if isinstance(b, dict))
    return str(msg)


def _strip_code_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[: -3]
    return s.strip()


def _run_browser_task(goal: str) -> dict:
    # Build a fresh agent per invocation so the ToolCallCap counter resets.
    raw = _message_text(_build_agent()(goal).message)
    cleaned = _strip_code_fence(raw)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        logger.warning("browser agent returned non-JSON output, wrapping raw text")
        return {"result": raw, "sources": [], "steps": ["unstructured_output"]}


app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload, context):
    goal = payload.get("goal") or payload.get("prompt") or payload.get("message") or ""
    return _run_browser_task(goal)


if __name__ == "__main__":
    app.run()
