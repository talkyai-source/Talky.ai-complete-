
import asyncio
import os
from dotenv import load_dotenv
from cartesia import AsyncCartesia

load_dotenv()

async def list_voices():
    api_key = os.getenv("CARTESIA_API_KEY")
    if not api_key:
        print("Error: CARTESIA_API_KEY not found")
        return

    print(f"Using API Key: {api_key[:5]}...")
    
    client = AsyncCartesia(api_key=api_key)
    
    try:
        # AsyncCartesia.voices.list() is apparently synchronous or returns a non-awaitable
        # IDs to check
        target_ids = {
            "f786b574-daa5-4673-aa0c-cbe3e8534c02": "Katie",
            "6ccbfb76-1fc6-48f7-b71d-91ac6298247b": "Aurora",
            "a0e99841-438c-4a64-b679-ae501e7d6091": "Sarah",
            "156fb8d2-335b-4950-9cb3-a2d33befec77": "Ryan",
            "22bc70c2-5c1a-4712-a72c-5b23e20ec619": "Jason",
            "820a3788-2b37-4d21-847a-b65dcfd43f05": "Michael"
        }
        
        found_ids = set()
        
        voices = client.voices.list()
        print(f"Found {len(voices)} voices total.")
        
        count = 0
        for v in voices:
            v_id = v['id'] if isinstance(v, dict) else v.id
            v_name = v['name'] if isinstance(v, dict) else v.name
            v_desc = v['description'] if isinstance(v, dict) else v.description
            print(f"VOICE: {v_name} | ID: {v_id} | DESC: {v_desc}")
            count += 1
            if count >= 10:
                break


            
    except Exception as e:
        print(f"Error fetching voices: {e}")
    
    # No close method on AsyncCartesia?
    # await client.close() 

if __name__ == "__main__":
    asyncio.run(list_voices())
