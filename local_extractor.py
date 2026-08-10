import asyncio
import json
import urllib.parse
from playwright.async_api import async_playwright
import os
import sys

SESSION_FILE = "session_master.json"

async def extract_session():
    print("Iniciando GETCID 2.0 - Extractor Local (Interceptador Dinámico)")
    print("Asegúrate de iniciar sesión con tu cuenta de Microsoft.")
    
    async with async_playwright() as p:
        user_data_dir = os.path.join(os.getcwd(), "playwright_data")
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        page = browser.pages[0] if browser.pages else await browser.new_page()
        
        tokens_captured = {}
        captured_client_id = None
        
        from core import DPoPEngine
        engine = DPoPEngine()
        
        async def on_route(route):
            nonlocal captured_client_id
            request = route.request
            
            if "oauth20_token.srf" in request.url.lower():
                # Si ya capturamos el token, bloqueamos posteriores
                if captured_client_id:
                    print("Bloqueando petición de token posterior para proteger el refresh_token...")
                    await route.abort()
                    return
                
                print("Inyectando DPoP persistente en la petición de token...")
                headers = request.headers
                dpop_proof = engine.generate_dpop_proof(request.method, request.url)
                headers["DPoP"] = dpop_proof
                # Asegurar que pedimos token_type=pop
                post_data = request.post_data
                if post_data and "token_type=pop" not in post_data:
                    post_data += "&token_type=pop"
                
                await route.continue_(headers=headers, post_data=post_data)
                return
                
            await route.continue_()

        async def on_response(response):
            nonlocal captured_client_id
            if "token" in response.url.lower() and response.status == 200:
                if captured_client_id: return # Ya tenemos uno
                
                try:
                    data = await response.json()
                    if "refresh_token" in data:
                        # Extraer el client_id de la petición
                        post_data = response.request.post_data or ""
                        parsed = urllib.parse.parse_qs(post_data)
                        client_id = parsed.get("client_id", [""])[0]
                        
                        if client_id:
                            print(f"✅ [NET] ¡Token capturado con éxito para el cliente: {client_id}!")
                            captured_client_id = client_id
                            tokens_captured["refresh_token"] = data["refresh_token"]
                            tokens_captured["access_token"] = data.get("access_token")
                            tokens_captured["client_id"] = client_id
                except:
                    pass

        await page.route("**/*", on_route)
        page.on("response", on_response)
        
        print("Navegando a la página de login de Microsoft...")
        await page.goto("https://account.microsoft.com/devices/recoverykey")
        
        print("\n" + "="*50)
        print("ACCIÓN REQUERIDA: Inicia sesión en la ventana del navegador.")
        print("   Solo inicia sesión normalmente. El script se cerrará solo")
        print("   en cuanto capture tu sesión.")
        print("="*50 + "\n")
        
        for _ in range(300):
            if captured_client_id:
                break
            await asyncio.sleep(1)
            
        if not captured_client_id:
            print("❌ No se detectó ninguna petición de token en la red. Asegúrate de iniciar sesión completamente.")
            await browser.close()
            return

        print("Guardando estado de la sesión...")
        
        storage_state = await browser.storage_state()
        export_data = {
            "storage_state": storage_state,
            "tokens_network": tokens_captured,
            "dpop_key": engine.get_pem_string()
        }
        
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=4)
            
        print(f"¡Éxito total! Archivo generado: {SESSION_FILE}")
        print("Lleva este archivo a tu servidor Ubuntu.")
        
        # Pequeña pausa para asegurar que se bloquean las peticiones en vuelo
        await asyncio.sleep(2)
        await browser.close()

if __name__ == "__main__":
    try:
        asyncio.run(extract_session())
    except KeyboardInterrupt:
        print("\nOperación cancelada por el usuario.")
    except Exception as e:
        print(f"\nError fatal: {str(e)}")
    finally:
        if sys.platform == 'win32':
            os.system("pause")
