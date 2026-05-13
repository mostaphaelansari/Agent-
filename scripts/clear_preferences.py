import os
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
    
    path = f"/strategies/{pref_strategy_id}/actors/default/"
    
    try:
        response = client.gmdp_client.list_memory_records(memoryId=memory_id, namespace=path)
        nodes = response.get("memoryRecordSummaries", [])
        
        if not nodes:
            print("No preferences found to delete.")
            return
            
        print(f"Found {len(nodes)} preference(s). Deleting them...")
        
        for node in nodes:
            record_id = node.get("memoryRecordId")
            print(f"Deleting record {record_id}...")
            client.gmdp_client.delete_memory_record(
                memoryId=memory_id,
                memoryRecordId=record_id
            )
            
        print("Successfully deleted all preferences!")
            
    except Exception as e:
        print(f"Error managing memory: {e}")

if __name__ == "__main__":
    main()
