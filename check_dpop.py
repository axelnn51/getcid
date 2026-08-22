import asyncio
import json
from playwright.async_api import async_playwright

async def check():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        async def on_request(request):
            if "token" in request.url.lower():
                print("TOKEN REQUEST TO:", request.url)
                print("HEADERS:", json.dumps(request.headers, indent=2))
                
        page.on("request", on_request)
        await page.goto("https://account.microsoft.com/devices/recoverykey")
        await asyncio.sleep(60)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(check())
