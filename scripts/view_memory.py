import os
import json
from dotenv import load_dotenv
from bedrock_agentcore.memory.client import MemoryClient

def main():
    load_dotenv()
    
    region = os.environ.get("AWS_REGION", "eu-west-1")
    memory_id = os.environ.get("AGENTCORE_MEMORY_ID")
    pref_strategy_id = os.environ.get("AGENTCORE_PREF_STRATEGY_ID")
    
    if not memory_id or not pref_strategy_id:
        print("Missing AGENTCORE_MEMORY_ID or AGENTCORE_PREF_STRATEGY_ID in .env")
        return

    print(f"Connecting to AWS Bedrock Memory in {region}...")
    client = MemoryClient(region_name=region)
    
    # By default, the actor_id is 'default' in your chatbot.py
    actor_id = "default"
    
    try:
        # Construct the path to the user preferences strategy for this actor
        path = f"/strategies/{pref_strategy_id}/actors/{actor_id}/"
        print(f"\nFetching preferences for actor '{actor_id}' at path: {path}")
        
        # We can list the nodes under this strategy path
        response = client.gmdp_client.list_memory_records(memoryId=memory_id, namespace=path)
        nodes = response.get("memoryRecordSummaries", [])
        
        print("\n--- Stored User Preferences ---")
        if not nodes:
            print("No preferences found.")
        
        for node in nodes:
            # Nodes contain content with the preferences
            content = node.get("content", {})
            print(content)
            
    except Exception as e:
        print(f"Error fetching memory: {e}")

if __name__ == "__main__":
    main()
