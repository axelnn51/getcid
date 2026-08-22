import asyncio
import json
import urllib.parse
from playwright.async_api import async_playwright

async def check():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        async def on_request(request):
            if "token" in request.url.lower() and request.method == "POST":
                print("\n=== TOKEN REQUEST INTERCEPTED ===")
                print("URL:", request.url)
                print("HEADERS:", json.dumps(request.headers, indent=2))
                if request.post_data:
                    parsed = urllib.parse.parse_qs(request.post_data)
                    print("PAYLOAD:", json.dumps(parsed, indent=2))
                print("=================================\n")
                
        page.on("request", on_request)
        await page.goto("https://account.microsoft.com/devices/recoverykey")
        print("Inicia sesión para capturar el payload...")
        await asyncio.sleep(120)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(check())
