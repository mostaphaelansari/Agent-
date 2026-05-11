import os
from strands import Agent, tool
from strands.models import BedrockModel
from agents.browser_agent import run_browser_task
from memory import save_turn, get_last_turns

@tool
def browse(goal: str) -> dict:
    """Run a web-browsing goal (lookup, scraping, form fill) and return the result."""
    return run_browser_task(goal)

chatbot = Agent(
    name="chatbot",
    model=BedrockModel(model_id=os.environ["BEDROCK_MODEL_ID"], region_name=os.environ["AWS_REGION"]),
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
    history = get_last_turns(session_id, k=10)
    context = "\n".join(f"{r}: {t}" for r, t in history)
    reply = _message_text(chatbot(context).message)
    save_turn(session_id, "assistant", reply)
    return reply
