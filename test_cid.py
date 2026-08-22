import asyncio
import sys
from core import get_cid_for_pid
from auth_http import auth_manager

async def test_cid():
    iid = "357924661076125925421879311561102041098650444344630147635232565"
    print(f"Probando IID: {iid}")
    if not auth_manager.access_token:
        print("Obteniendo token de acceso...")
        await auth_manager.refresh_access_token()
    
    try:
        cid = await get_cid_for_pid(iid, auth_manager.access_token, auth_manager.dpop_manager)
        print(f"CID OBTENIDO: {cid}")
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test_cid())
