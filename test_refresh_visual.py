import asyncio
import json
import httpx
from core import DPoPEngine

async def test_refresh():
    with open('session_master.json', 'r') as f:
        master_data = json.load(f)
    rt = None
    for origin in master_data.get('storage_state', {}).get('origins', []):
        for ls in origin.get('localStorage', []):
            if 'refreshtoken|81feaced' in ls['name']:
                val = json.loads(ls['value'])
                rt = val.get('data', '')
                break
        if rt: break

    print("Extracted RT:", rt[:10] + "..." if rt else "None")

    engine = DPoPEngine()
    dpop = engine.generate_dpop_proof("POST", "https://login.live.com/oauth20_token.srf")

    payload = {
        "client_id": "81feaced-5ddd-41e7-8bef-3e20a2689bb7",
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "token_type": "pop"
    }

    headers = {
        "DPoP": dpop,
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://account.microsoft.com"
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post("https://login.live.com/oauth20_token.srf", data=payload, headers=headers)
        print("Status:", resp.status_code)
        print("Response keys:", resp.json().keys() if resp.status_code == 200 else resp.json())

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test_refresh())
