import os
from strands import Agent, tool
from strands.models import BedrockModel
from agents.browser_agent import run_browser_task

@tool
def call_browser_agent(goal: str) -> dict:
    """Delegate a web-browsing goal to the Browser Agent."""
    return run_browser_task(goal)

orchestrator = Agent(
    name="orchestrator",
    model=BedrockModel(model_id=os.environ["BEDROCK_MODEL_ID"], region_name=os.environ["AWS_REGION"]),
    tools=[call_browser_agent],
    system_prompt=(
        "You are an orchestrator. Decompose the task, pick the right specialist, "
        "and return a structured result."
    ),
)

def run_orchestration(task: str) -> str:
    return orchestrator(task).message
