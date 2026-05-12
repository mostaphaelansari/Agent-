"""One-shot provisioning of the AgentCore Memory resource.

Run once per environment. Prints the memory_id to add to .env and yaml.

Usage:
    AWS_PROFILE=default python scripts/provision_memory.py
"""

import logging
import os
import sys

from dotenv import load_dotenv

from bedrock_agentcore.memory.client import MemoryClient

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("provision_memory")

MEMORY_NAME = "multi_agent_chatbot_memory"
REGION = os.environ.get("AWS_REGION", "eu-west-1")
EVENT_EXPIRY_DAYS = 30

SUMMARY_STRATEGY = {
    "summaryMemoryStrategy": {
        "name": "ConversationSummary",
        "description": "Periodic summary of the conversation per session",
    }
}

USER_PREF_STRATEGY = {
    "userPreferenceMemoryStrategy": {
        "name": "UserPreferences",
        "description": "Extracted user preferences shared across sessions",
    }
}


def main() -> int:
    client = MemoryClient(region_name=REGION)
    logger.info("creating/fetching memory '%s' in %s", MEMORY_NAME, REGION)

    memory = client.create_or_get_memory(
        name=MEMORY_NAME,
        strategies=[SUMMARY_STRATEGY, USER_PREF_STRATEGY],
        description="Conversational memory for multi_agent_chatbot",
        event_expiry_days=EVENT_EXPIRY_DAYS,
    )

    memory_id = memory.get("id") or memory.get("memoryId")
    memory_arn = memory.get("arn") or memory.get("memoryArn")
    status = memory.get("status")

    logger.info("memory ready: id=%s status=%s", memory_id, status)
    print()
    print("=" * 60)
    print(f"AGENTCORE_MEMORY_ID={memory_id}")
    print(f"AGENTCORE_MEMORY_ARN={memory_arn}")
    print("=" * 60)
    print("Add AGENTCORE_MEMORY_ID to .env and to --env on agentcore launch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
