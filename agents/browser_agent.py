import os
from functools import lru_cache

from strands import Agent
from strands.models import BedrockModel
from strands_tools.browser import AgentCoreBrowser


@lru_cache(maxsize=1)
def _get_browser_agent() -> Agent:
    region = os.environ["AWS_REGION"]
    browser_tool = AgentCoreBrowser(region=region)
    return Agent(
        name="browser",
        model=BedrockModel(model_id=os.environ["BEDROCK_MODEL_ID"], region_name=region),
        tools=[browser_tool.browser],
        system_prompt=(
            "You are a browser automation specialist. Given a goal, navigate the web "
            "and return STRICT JSON: {result, steps, session_url}. No chitchat."
        ),
    )


def run_browser_task(goal: str) -> dict:
    return {"output": _get_browser_agent()(goal).message}
