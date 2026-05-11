import logging
import os

from strands import Agent, tool
from strands.models import BedrockModel

from agents.browser_agent import run_browser_task
from memory import build_context, save_turn

logger = logging.getLogger(__name__)


@tool
def browse(goal: str) -> dict:
    """Run a web-browsing goal (lookup, scraping, form fill) and return the result."""
    return run_browser_task(goal)


chatbot = Agent(
    name="chatbot",
    model=BedrockModel(
        model_id=os.environ["BEDROCK_MODEL_ID"],
        region_name=os.environ["AWS_REGION"],
    ),
    tools=[browse],
    system_prompt=(
        "You are a friendly chatbot. Chat naturally for small talk. "
        "When the user wants something done on the web, call `browse` "
        "with a precise goal and summarize the result."
    ),
)


def _message_text(msg) -> str:
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict) and isinstance(msg.get("content"), list):
        return "\n".join(b.get("text", "") for b in msg["content"] if isinstance(b, dict))
    return str(msg)


def chat(session_id: str, user_msg: str) -> str:
    save_turn(session_id, "user", user_msg)
    context = build_context(session_id, k=10, max_chars=8000)
    try:
        reply = _message_text(chatbot(context).message)
    except Exception:
        logger.exception("chatbot call failed for session %s", session_id)
        reply = "Sorry, something went wrong on my side. Please try again."
    save_turn(session_id, "assistant", reply)
    return reply
