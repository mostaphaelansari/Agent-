import os
from strands import Agent, tool
from strands.models import BedrockModel
from agents.orchestrator_agent import run_orchestration
from memory import save_turn, get_last_turns

@tool
def delegate(task: str) -> str:
    """Send an action task (web lookup, scraping, form fill) to the orchestrator."""
    return run_orchestration(task)

chatbot = Agent(
    name="chatbot",
    model=BedrockModel(model_id=os.environ["BEDROCK_MODEL_ID"], region_name=os.environ["AWS_REGION"]),
    tools=[delegate],
    system_prompt=(
        "You are a friendly chatbot. Chat naturally for small talk. "
        "When the user wants something done on the web, call `delegate` "
        "with a precise task brief and summarize the result."
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
