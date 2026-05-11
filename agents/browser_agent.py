import os
from strands import Agent
from strands.models import BedrockModel
from strands_tools.browser import AgentCoreBrowser

_REGION = os.environ["AWS_REGION"]
_browser_tool = AgentCoreBrowser(region=_REGION)

browser_agent = Agent(
    name="browser",
    model=BedrockModel(model_id=os.environ["BEDROCK_MODEL_ID"], region_name=_REGION),
    tools=[_browser_tool.browser],
    system_prompt=(
        "You are a browser automation specialist. Given a goal, navigate the web "
        "and return STRICT JSON: {result, steps, session_url}. No chitchat."
    ),
)

def run_browser_task(goal: str) -> dict:
    return {"output": browser_agent(goal).message}
