import asyncio
import json
import urllib.parse
from playwright.async_api import async_playwright
import os
import sys

async def capture():
    print("Iniciando captura de tráfico. Sigue los pasos en el navegador...")
    async with async_playwright() as p:
        user_data_dir = os.path.join(os.getcwd(), "playwright_data")
        
        # Usamos el contexto persistente donde el usuario ya inició sesión antes
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False, # Modo visible para que el usuario pueda interactuar
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        page = await browser.new_page()
        api_found = False
        
        async def on_request(request):
            nonlocal api_found
            if "validateiid" in request.url.lower():
                api_found = True
                print("\n" + "="*50)
                print(f"URL DE LA API: {request.url}")
                print("HEADERS ENVIADOS:")
                for k, v in request.headers.items():
                    print(f"  {k}: {v}")
                    
                print("\nPAYLOAD ENVIADO:")
                print(request.post_data)
                print("==================================================\n")
                
        page.on("request", on_request)
        
        print("1. Inicia sesión en la página.")
        print("2. Intenta ingresar un Installation ID (IID) y presiona el botón para validar.")
        await page.goto("https://visualsupport.microsoft.com/")
        
        # Esperamos hasta capturar la API
        for _ in range(120):
            if api_found:
                break
            await asyncio.sleep(1)
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(capture())
