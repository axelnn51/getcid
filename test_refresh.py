import asyncio
import json
import httpx
from core import DPoPEngine

async def test_refresh():
    with open('session_master.json', 'r') as f:
        master_data = json.load(f)
    rt = master_data.get('tokens_network', {}).get('refresh_token')
    cid = master_data.get('tokens_network', {}).get('client_id')
    print(f"Client ID: {cid}")

    engine = DPoPEngine()
    dpop = engine.generate_dpop_proof("POST", "https://login.live.com/oauth20_token.srf")

    payload = {
        "client_id": cid,
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
        data = resp.json()
        print("Token Type:", data.get('token_type'))
        if 'error' in data:
            print("Error:", data)

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test_refresh())
