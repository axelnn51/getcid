import asyncio
import json
import httpx
from playwright.async_api import async_playwright
import os
import sys

SESSION_FILE = "session_master.json"

async def extract_session():
    print("Iniciando GETCID 2.0 - Extractor Local (Código de Autorización)")
    print("Asegúrate de iniciar sesión con tu cuenta de Microsoft.")
    
    async with async_playwright() as p:
        user_data_dir = os.path.join(os.getcwd(), "playwright_data")
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        page = browser.pages[0] if browser.pages else await browser.new_page()
        
        code_captured = None
        
        async def on_response(response):
            nonlocal code_captured
            if "oauth20_desktop.srf" in response.url and "code=" in response.url:
                try:
                    code_captured = response.url.split("code=")[1].split("&")[0]
                    print("✅ [NET] ¡Código de Autorización interceptado con éxito!")
                except:
                    pass

        page.on("response", on_response)
        
        print("Navegando a la página de login de Microsoft...")
        # Usamos el cliente genérico que permite redirección a desktop.srf
        auth_url = "https://login.live.com/oauth20_authorize.srf?client_id=29d9ed98-a469-4536-ade2-f981bc1d605e&response_type=code&redirect_uri=https://login.live.com/oauth20_desktop.srf&scope=service::http://Passport.NET/tb::PURPOSE offline_access"
        await page.goto(auth_url)
        
        print("\n" + "="*50)
        print("ACCIÓN REQUERIDA: Inicia sesión en la ventana del navegador.")
        print("   Solo inicia sesión normalmente. El script se cerrará solo")
        print("   en cuanto capture el código de autorización.")
        print("="*50 + "\n")
        
        # Esperar hasta capturar el código (máximo 5 minutos)
        for _ in range(300):
            if code_captured:
                break
            await asyncio.sleep(1)
            
        if not code_captured:
            print("❌ No se pudo capturar el código. Tiempo de espera agotado o ventana cerrada.")
            await browser.close()
            return

        print("Intercambiando código por Token Maestro...")
        
        token_url = "https://login.live.com/oauth20_token.srf"
        payload = {
            "client_id": "29d9ed98-a469-4536-ade2-f981bc1d605e",
            "grant_type": "authorization_code",
            "code": code_captured,
            "redirect_uri": "https://login.live.com/oauth20_desktop.srf"
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(token_url, data=payload)
            if resp.status_code == 200:
                data = resp.json()
                print("✅ ¡Token Maestro generado exitosamente!")
                
                # Guardamos el estado
                storage_state = await browser.storage_state()
                export_data = {
                    "storage_state": storage_state,
                    "tokens_network": {
                        "refresh_token": data.get("refresh_token"),
                        "access_token": data.get("access_token")
                    }
                }
                
                with open(SESSION_FILE, "w", encoding="utf-8") as f:
                    json.dump(export_data, f, indent=4)
                    
                print(f"¡Éxito total! Archivo generado: {SESSION_FILE}")
                print("Lleva este archivo a tu servidor Ubuntu.")
            else:
                print(f"❌ Falló el intercambio de token: {resp.status_code} - {resp.text}")
        
        
        await browser.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(extract_session())
