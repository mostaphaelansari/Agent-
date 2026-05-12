import logging
import os

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

import telemetry_setup  # noqa: F401  (must come before agent imports)

from bedrock_agentcore import BedrockAgentCoreApp
from agents.chatbot import chat

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload, context):
    session_id = getattr(context, "session_id", None) or "default"
    user_msg = payload.get("prompt") or payload.get("message") or ""
    actor_id = payload.get("actor_id") or "default"
    return {"result": chat(session_id, user_msg, actor_id=actor_id)}


if __name__ == "__main__":
    app.run()
