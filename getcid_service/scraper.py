import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
import os
from dotenv import load_dotenv
import logging
import json
import time

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("GetCID_Scraper")

# Soporte para una sola cuenta o múltiples cuentas rotativas
ms_accounts_env = os.getenv("MS_ACCOUNTS")
single_email = os.getenv("MS_EMAIL")
single_pass = os.getenv("MS_PASSWORD")

ACCOUNTS = []
if ms_accounts_env:
    for acc in ms_accounts_env.split(","):
        if ":" in acc:
            e, p = acc.split(":", 1)
            ACCOUNTS.append({"email": e.strip(), "password": p.strip()})
elif single_email and single_pass:
    ACCOUNTS.append({"email": single_email.strip(), "password": single_pass.strip()})

STATE_DIR = "states"
TOKEN_CACHE_FILE = "ms_token.json"

if not os.path.exists(STATE_DIR):
    os.makedirs(STATE_DIR)

async def attempt_login_for_account(p, account: dict, is_first_account: bool) -> str:
    """Intenta iniciar sesión o usar la sesión guardada para una cuenta específica."""
    email = account['email']
    password = account['password']
    state_file = os.path.join(STATE_DIR, f"state_{email.replace('@', '_').replace('.', '_')}.json")
    
    needs_ui = not os.path.exists(state_file)
    
    # En Ubuntu Server, forzar headless siempre, o solo UI si es la primera cuenta y estamos local
    # Para rotación automática segura, intentaremos headless primero si se puede.
    # Pero si needs_ui es True y estamos en un server (asumido si hay multiples cuentas), 
    # la rotación intentará loguearse vía UI automática. Si pide captcha, fallará rápido.
    is_headless = True
    
    if needs_ui and is_first_account and len(ACCOUNTS) == 1:
        # Modo interactivo solo si es la única cuenta y primera vez (para PC local)
        is_headless = False

    logger.info(f"Probando cuenta: {email} (Headless: {is_headless})")
    
    browser = await p.chromium.launch(headless=is_headless, args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-blink-features=AutomationControlled'])
    
    context_options = {}
    if not needs_ui:
        context_options['storage_state'] = state_file
        
    context = await browser.new_context(
        **context_options,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = await context.new_page()
    
    # Inyectar Stealth para evadir detección antibot de Microsoft
    await stealth_async(page)
    
    captured_token = None

    async def handle_request(request):
        nonlocal captured_token
        if "api/productActivation" in request.url or "visualsupport.microsoft.com/api/" in request.url:
            auth_header = request.headers.get("authorization")
            if auth_header and "Bearer" in auth_header:
                captured_token = auth_header.replace("Bearer ", "").strip()
                logger.info(f"[{email}] ¡Token Bearer capturado exitosamente!")

    page.on("request", handle_request)

    try:
        await page.goto("https://visualsupport.microsoft.com/", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        if needs_ui and not is_headless:
            # Login manual guiado
            logger.warning(f"[{email}] Esperando que completes el login interactivo/CAPTCHA...")
            for _ in range(60):
                if captured_token: break
                await page.wait_for_timeout(5000)
                if "login.microsoftonline.com" in page.url:
                    try:
                        if await page.locator("input[type='email']").is_visible(timeout=1000):
                            await page.locator("input[type='email']").fill(email)
                        if await page.locator("input[type='password']").is_visible(timeout=1000):
                            await page.locator("input[type='password']").fill(password)
                    except: pass
            
            if captured_token or "visualsupport.microsoft.com/productActivation" in page.url:
                await context.storage_state(path=state_file)
                logger.info(f"[{email}] Estado de sesión guardado permanentemente.")
        else:
            # Flujo automatizado / Headless
            btn_text = await page.locator("body").inner_text()
            if "Let" in btn_text and "Started" in btn_text:
                await page.get_by_role("button").first.click()
                
            # Intentar login automático si aparece
            for _ in range(4):
                if "login.microsoftonline.com" in page.url:
                    if await page.locator("input[type='email']").is_visible(timeout=2000):
                        await page.locator("input[type='email']").fill(email)
                        await page.locator("input[type='submit']").click()
                        await page.wait_for_timeout(2000)
                    
                    if await page.locator("input[type='password']").is_visible(timeout=3000):
                        await page.locator("input[type='password']").fill(password)
                        await page.locator("input[type='submit']").click()
                        await page.wait_for_timeout(3000)
                        
                        if await page.locator("input[id='idBtn_Back']").is_visible(timeout=3000):
                            await page.locator("input[id='idBtn_Back']").click()
                            
                    await context.storage_state(path=state_file)
                    break
                
                # Si pide Captcha explícito en la URL o DOM
                if "captchaa" in page.url.lower():
                    logger.error(f"[{email}] Microsoft detectó bot y pide CAPTCHA. Abandonando cuenta.")
                    return None
                    
                await page.wait_for_timeout(2000)

            await page.wait_for_timeout(6000)

        # Fallback a Session Storage
        if not captured_token:
            session_storage = await page.evaluate("() => JSON.stringify(window.sessionStorage)")
            if session_storage:
                ss_data = json.loads(session_storage)
                for key, value in ss_data.items():
                    if "AccessToken" in key or "accesstoken" in key.lower() or "secret" in value:
                        try:
                            token_data = json.loads(value)
                            if "secret" in token_data:
                                captured_token = token_data["secret"]
                                logger.info(f"[{email}] Token extraído del caché MSAL.")
                                break
                        except: pass

    except Exception as e:
        logger.error(f"[{email}] Error en Playwright: {e}")
    finally:
        await browser.close()
        return captured_token

async def extract_ms_token() -> str:
    if not ACCOUNTS:
        logger.error("No hay cuentas configuradas en .env (MS_ACCOUNTS o MS_EMAIL)")
        return None

    if os.path.exists(TOKEN_CACHE_FILE):
        try:
            with open(TOKEN_CACHE_FILE, 'r') as f:
                data = json.load(f)
                if data.get('expires_at', 0) > time.time():
                    return data.get('token')
        except Exception as e:
            logger.warning(f"Error leyendo caché de token: {e}")

    logger.info("Iniciando motor Playwright Stealth para rotación de cuentas...")
    
    async with async_playwright() as p:
        for index, account in enumerate(ACCOUNTS):
            token = await attempt_login_for_account(p, account, is_first_account=(index == 0))
            if token:
                with open(TOKEN_CACHE_FILE, 'w') as f:
                    json.dump({
                        'token': token,
                        'expires_at': time.time() + 3000
                    }, f)
                return token
            else:
                logger.warning(f"Cuenta {account['email']} falló. Pasando a la siguiente...")
                
        logger.error("¡ALERTA CRÍTICA! Todas las cuentas fallaron o pidieron CAPTCHA.")
        # Aquí se podría integrar la alerta de Telegram
        return None

if __name__ == "__main__":
    async def test():
        token = await extract_ms_token()
        print(f"Token final: {token[:20]}..." if token else "Fallo crítico al extraer.")
    asyncio.run(test())
