import asyncio
import json
from playwright.async_api import async_playwright
import os
import sys

SESSION_FILE = "session_master.json"

async def extract_session():
    print("🚀 Iniciando GETCID 2.0 - Extractor Local")
    print("Asegúrate de iniciar sesión con tu cuenta de Microsoft.")
    
    async with async_playwright() as p:
        # Usamos un contexto persistente para guardar la sesión
        user_data_dir = os.path.join(os.getcwd(), "playwright_data")
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
            ]
        )
        
        page = browser.pages[0] if browser.pages else await browser.new_page()
        
        # Opcional: Escuchar respuestas de red para atrapar tokens OAuth si están en el payload
        tokens_captured = {}
        
        async def handle_response(response):
            if "oauth2/v2.0/token" in response.url and response.status == 200:
                try:
                    data = await response.json()
                    if "refresh_token" in data:
                        print("✅ [NET] Refresh Token capturado desde la red.")
                        tokens_captured["refresh_token"] = data["refresh_token"]
                        tokens_captured["access_token"] = data.get("access_token")
                except:
                    pass

        page.on("response", handle_response)
        
        print("🌐 Navegando a la página de login de Microsoft...")
        # Página típica de login de Microsoft
        await page.goto("https://login.live.com/")
        
        print("\n" + "="*50)
        print("⏳ ACCIÓN REQUERIDA: Inicia sesión en la ventana del navegador.")
        print("   Una vez que estés dentro y veas tu cuenta de Microsoft,")
        print("   vuelve a esta ventana de consola y presiona ENTER.")
        print("="*50 + "\n")
        
        # Esperar confirmación manual del usuario
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input, "Presiona ENTER aquí cuando hayas terminado...")
        print("✅ Confirmación recibida.")
        
        print("💾 Guardando estado de la sesión (Cookies y Session/Local Storage)...")
        storage_state = await browser.storage_state()
        
        # Mezclamos la información de red capturada (si la hay) con el state
        export_data = {
            "storage_state": storage_state,
            "tokens_network": tokens_captured
        }
        
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=4)
            
        print(f"🎉 ¡Éxito! Archivo generado: {SESSION_FILE}")
        print("Lleva este archivo a tu servidor Ubuntu.")
        
        await browser.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(extract_session())
