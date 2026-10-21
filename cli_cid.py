import sys
import json
import asyncio
from batch_cid import get_cid

async def main():
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "error": "No IID provided"}))
        return
    iid = sys.argv[1].strip()
    try:
        res = await get_cid(iid)
        print(json.dumps(res))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))

if __name__ == "__main__":
    asyncio.run(main())
