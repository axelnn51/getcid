import asyncio
import json
import urllib.parse
from playwright.async_api import async_playwright
import os
import sys
import requests
import time
from core import DPoPEngine

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN = os.getenv("TELEGRAM_ADMIN_ID")
MS_EMAIL = os.getenv("MS_EMAIL")
MS_PASSWORD = os.getenv("MS_PASSWORD")

def send_telegram_alert(msg):
    if TELEGRAM_TOKEN and TELEGRAM_ADMIN:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_ADMIN, "text": msg, "parse_mode": "Markdown"})

async def extract_session():
    print("Iniciando Auto Extractor (noVNC)...")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir="/app/playwright_data",
            headless=False, # Xvfb se encarga de renderizar esto en :99
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
        )
        
        page = browser.pages[0] if browser.pages else await browser.new_page()
        
        tokens_captured = {}
        captured_client_id = None
        engine = DPoPEngine()
        
        async def on_route(route):
            nonlocal captured_client_id
            request = route.request
            
            if "common/oauth2/v2.0/token" in request.url.lower():
                if captured_client_id:
                    await route.abort()
                    return
                
                headers = request.headers
                dpop_proof = engine.generate_dpop_proof(request.method, request.url)
                headers["DPoP"] = dpop_proof
                
                post_data = request.post_data
                if post_data and "token_type=pop" not in post_data:
                    post_data += "&token_type=pop"
                    
                await route.continue_(headers=headers, post_data=post_data)
                return
                
            await route.continue_()

        page.on("route", on_route)

        async def on_response(response):
            nonlocal tokens_captured, captured_client_id
            if "common/oauth2/v2.0/token" in response.url.lower() and response.request.method == "POST":
                if captured_client_id: return
                
                try:
                    data = await response.json()
                    if "refresh_token" in data:
                        post_data = response.request.post_data or ""
                        parsed = urllib.parse.parse_qs(post_data)
                        client_id = parsed.get("client_id", [""])[0]
                        
                        if client_id:
                            print(f"✅ Token capturado: {client_id}")
                            captured_client_id = client_id
                            tokens_captured["refresh_token"] = data["refresh_token"]
                            tokens_captured["access_token"] = data.get("access_token")
                            tokens_captured["client_id"] = client_id
                except:
                    pass

        await page.route("**/*", on_route)
        page.on("response", on_response)
        
        # Automatización de Login
        try:
            print("Navegando a la página de login...")
            await page.goto("https://visualsupport.microsoft.com/", wait_until="networkidle")
            await asyncio.sleep(2)
            
            # Buscar el campo de email (si no está logueado)
            email_input = page.locator("input[type='email']")
            if await email_input.count() > 0:
                print("Escribiendo email...")
                await email_input.fill(MS_EMAIL)
                await page.keyboard.press("Enter")
                await asyncio.sleep(3)
                
            # Buscar el campo de contraseña
            pass_input = page.locator("input[type='password']")
            if await pass_input.count() > 0:
                print("Escribiendo contraseña...")
                await pass_input.fill(MS_PASSWORD)
                await page.keyboard.press("Enter")
                await asyncio.sleep(3)
                
                # Clicar "Yes" en "Stay signed in?" si aparece
                yes_btn = page.locator("input[id='idSIButton9']")
                if await yes_btn.count() > 0:
                    print("Haciendo click en 'Stay signed in'...")
                    await yes_btn.click()
                    
        except Exception as e:
            print(f"Aviso en automatización (puede que ya estuviera logueado): {e}")

        print("Esperando a capturar el token...")
        
        # Esperar 20 segundos a ver si lo captura automático
        for _ in range(20):
            if captured_client_id:
                break
            await asyncio.sleep(1)
            
        if not captured_client_id:
            # Asumimos que se atascó (CAPTCHA o Arkose Labs)
            print("Posible CAPTCHA detectado. Enviando alerta a Telegram...")
            send_telegram_alert("🚨 *GETCID Bot Atascado en Login*\n\nEl servidor requiere resolver un CAPTCHA o verificar la cuenta.\n\n👉 Entra al servidor por el puerto `6080` (ej: `http://TU_IP:6080/vnc.html`) para resolverlo manualmente.\n\nEl bot te esperará indefinidamente...")
            
        # Bucle infinito hasta que el humano lo resuelva y el token caiga
        while not captured_client_id:
            await asyncio.sleep(2)
            
        print("Token capturado. Enviando sesión al backend principal...")
        
        storage_state = await browser.storage_state()
        export_data = {
            "storage_state": storage_state,
            "tokens_network": tokens_captured,
            "dpop_key": engine.get_pem_string()
        }
        
        # Enviar al backend local de FastAPI
        try:
            resp = requests.post("http://getcid_backend:8000/api/update_session", json=export_data)
            print(f"Backend response: {resp.text}")
            send_telegram_alert("✅ *Token recuperado con éxito*\n\nEl servidor de GETCID vuelve a operar con normalidad.")
        except Exception as e:
            print(f"Error enviando al backend: {e}")
        
        await asyncio.sleep(2)
        await browser.close()

if __name__ == "__main__":
    if not MS_EMAIL or not MS_PASSWORD:
        print("ERROR: MS_EMAIL y MS_PASSWORD no definidos en el entorno.")
        send_telegram_alert("❌ Error: MS_EMAIL o MS_PASSWORD no están configurados en el docker-compose.")
        sys.exit(1)
    asyncio.run(extract_session())
