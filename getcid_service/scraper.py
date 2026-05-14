import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
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
    
    # IS_SERVER=true en Docker → login 100% automatizado (Xvfb provee pantalla virtual)
    # En local (sin esa variable) → login manual si no hay sesión (ventana visible para el usuario)
    # CLAVE: headless=False SIEMPRE. En servidor, Xvfb simula la pantalla.
    # Microsoft NO puede distinguir esto de un navegador real → no lanza CAPTCHA.
    is_server = os.getenv("IS_SERVER", "false").lower() == "true"
    is_headless = False  # NUNCA headless. Xvfb en servidor, pantalla real en local.
    
    if needs_ui and not is_server:
        logger.warning(f"[{email}] No hay sesión guardada. Abriendo Chrome para login manual...")
    elif needs_ui and is_server:
        logger.warning(f"[{email}] No hay sesión guardada en servidor. Intentando login automatizado con Xvfb...")

    logger.info(f"Probando cuenta: {email} (Headless: {is_headless}, Server: {is_server})")
    
    browser = await p.chromium.launch(headless=is_headless, args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-blink-features=AutomationControlled', '--disable-dev-shm-usage'])
    
    context_options = {}
    if not needs_ui:
        context_options['storage_state'] = state_file
        
    context = await browser.new_context(
        **context_options,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = await context.new_page()
    
    # Inyectar Stealth para evadir detección antibot de Microsoft
    stealth = Stealth()
    await stealth.apply_stealth_async(page)
    
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

        if needs_ui and not is_server:
            # ===== RUTA 1: Login manual en PC local (con ventana visible) =====
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
            
            if captured_token or "visualsupport.microsoft.com" in page.url:
                await context.storage_state(path=state_file)
                logger.info(f"[{email}] Estado de sesión guardado permanentemente.")

        elif not needs_ui:
            # ===== RUTA 2: Sesión guardada existe (cookies cargadas) =====
            logger.info(f"[{email}] Sesión guardada cargada. Esperando que cookies autentiquen...")
            
            # Dar tiempo generoso a las cookies para que hagan el handshake OAuth
            for i in range(15):  # Máximo 30 segundos
                if captured_token:
                    logger.info(f"[{email}] Token capturado via cookies en {i*2}s")
                    break
                
                current_url = page.url
                logger.info(f"[{email}] Iteración {i+1}/15 - URL: {current_url[:80]}...")
                
                # Si estamos en visualsupport, buscar botón "Let's Get Started"
                if "visualsupport.microsoft.com" in current_url and "login" not in current_url:
                    try:
                        btn = page.get_by_role("button", name="Get Started")
                        if await btn.is_visible(timeout=1000):
                            await btn.click()
                            logger.info(f"[{email}] Botón 'Get Started' clickeado.")
                            await page.wait_for_timeout(3000)
                            continue
                    except: pass
                    
                    # También intentar cualquier botón visible
                    try:
                        body_text = await page.locator("body").inner_text(timeout=2000)
                        if "Let" in body_text and "Started" in body_text:
                            await page.get_by_role("button").first.click()
                            await page.wait_for_timeout(3000)
                            continue
                    except: pass
                
                # Si nos redirigió a login, las cookies expiraron → intentar login automático
                if "login.microsoftonline.com" in current_url and i > 3:
                    logger.warning(f"[{email}] Cookies expiraron. Intentando login automático...")
                    try:
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
                        logger.info(f"[{email}] Sesión renovada y guardada.")
                    except Exception as login_err:
                        logger.warning(f"[{email}] Login automático falló: {login_err}")
                    
                await page.wait_for_timeout(2000)

        else:
            # ===== RUTA 3: Sin sesión en servidor (primer uso) =====
            logger.warning(f"[{email}] Sin sesión en servidor. Login automático con Xvfb...")
            
            # Buscar botón "Let's Get Started"
            try:
                body_text = await page.locator("body").inner_text(timeout=3000)
                if "Let" in body_text and "Started" in body_text:
                    await page.get_by_role("button").first.click()
                    await page.wait_for_timeout(3000)
            except: pass
            
            # Intentar login automático
            for attempt in range(6):
                if captured_token: break
                
                if "login.microsoftonline.com" in page.url:
                    # Verificar CAPTCHA real (solo por imagen de captcha, no por URL genérica)
                    try:
                        captcha_img = page.locator("img[id*='captcha'], img[id*='hip'], #hipTemplateContainer")
                        if await captcha_img.is_visible(timeout=1500):
                            logger.error(f"[{email}] CAPTCHA visual detectado. Abandonando cuenta.")
                            try:
                                await page.screenshot(path="/app/debug_captcha.png")
                            except: pass
                            return None
                    except: pass
                    
                    try:
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
                        logger.info(f"[{email}] Sesión creada y guardada en servidor.")
                        break
                    except Exception as e:
                        logger.warning(f"[{email}] Intento {attempt+1} de login falló: {e}")
                
                await page.wait_for_timeout(3000)
            
            await page.wait_for_timeout(5000)

        # ===== Fallback: extraer token de Session Storage / Local Storage =====
        if not captured_token:
            logger.info(f"[{email}] Token no capturado por interceptor. Buscando en storage...")
            try:
                # Session Storage
                session_storage = await page.evaluate("() => JSON.stringify(window.sessionStorage)")
                if session_storage:
                    ss_data = json.loads(session_storage)
                    for key, value in ss_data.items():
                        if "AccessToken" in key or "accesstoken" in key.lower():
                            try:
                                token_data = json.loads(value)
                                if "secret" in token_data:
                                    captured_token = token_data["secret"]
                                    logger.info(f"[{email}] Token extraído de sessionStorage (MSAL).")
                                    break
                            except: pass
                
                # Local Storage fallback
                if not captured_token:
                    local_storage = await page.evaluate("() => JSON.stringify(window.localStorage)")
                    if local_storage:
                        ls_data = json.loads(local_storage)
                        for key, value in ls_data.items():
                            if "AccessToken" in key or "accesstoken" in key.lower():
                                try:
                                    token_data = json.loads(value)
                                    if "secret" in token_data:
                                        captured_token = token_data["secret"]
                                        logger.info(f"[{email}] Token extraído de localStorage.")
                                        break
                                except: pass
            except Exception as storage_err:
                logger.warning(f"[{email}] Error buscando en storage: {storage_err}")
            
            if not captured_token:
                logger.error(f"[{email}] No se pudo capturar token. URL final: {page.url}")
                try:
                    await page.screenshot(path="/app/debug_no_token.png")
                    logger.info(f"[{email}] Screenshot de debug guardado en /app/debug_no_token.png")
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

    # 1. Verificar si hay un access token vigente en caché
    if os.path.exists(TOKEN_CACHE_FILE):
        try:
            with open(TOKEN_CACHE_FILE, 'r') as f:
                data = json.load(f)
                if data.get('expires_at', 0) > time.time():
                    logger.info("Access token en caché aún válido.")
                    return data.get('token')
        except Exception as e:
            logger.warning(f"Error leyendo caché de token: {e}")

    # 2. Intentar renovar con Refresh Token (sin navegador, sin CAPTCHA)
    try:
        from token_refresher import refresh_access_token
        logger.info("Intentando renovar access token con refresh token...")
        new_token = await refresh_access_token()
        if new_token:
            logger.info("Access token renovado exitosamente via refresh token. Sin CAPTCHA!")
            return new_token
        else:
            logger.warning("Refresh token no disponible o expirado. Cayendo a Playwright...")
    except ImportError:
        logger.warning("Módulo token_refresher no disponible.")
    except Exception as e:
        logger.warning(f"Error en refresh token: {e}")

    # 3. Último recurso: Playwright (solo funciona en local o con CAPTCHA solver)
    logger.info("Iniciando motor Playwright Stealth para rotación de cuentas...")
    
    async with async_playwright() as p:
        for index, account in enumerate(ACCOUNTS):
            token = await attempt_login_for_account(p, account, is_first_account=(index == 0))
            if token:
                # Guardar access token
                with open(TOKEN_CACHE_FILE, 'w') as f:
                    json.dump({
                        'token': token,
                        'expires_at': time.time() + 3000
                    }, f)
                
                # Extraer y guardar refresh token para uso futuro
                await extract_refresh_token_from_cache(account['email'])
                
                return token
            else:
                logger.warning(f"Cuenta {account['email']} falló. Pasando a la siguiente...")
                
        logger.error("¡ALERTA CRÍTICA! Todas las cuentas fallaron o pidieron CAPTCHA.")
        return None


async def extract_refresh_token_from_cache(email: str):
    """
    Extrae el Refresh Token del caché MSAL guardado en sessionStorage/localStorage
    durante el login con Playwright. Lo guarda para uso futuro en el servidor.
    """
    state_file = os.path.join(STATE_DIR, f"state_{email.replace('@', '_').replace('.', '_')}.json")
    
    if not os.path.exists(state_file):
        return
    
    try:
        with open(state_file, 'r') as f:
            state_data = json.load(f)
        
        # Buscar refresh token en los origins del storage state
        for origin_data in state_data.get('origins', []):
            for item in origin_data.get('localStorage', []):
                value = item.get('value', '')
                name = item.get('name', '')
                
                # Buscar entradas MSAL con RefreshToken
                if 'RefreshToken' in name or 'refreshtoken' in name.lower():
                    try:
                        token_data = json.loads(value)
                        if 'secret' in token_data:
                            # Buscar client_id en la misma storage
                            client_id = token_data.get('client_id', '')
                            if not client_id:
                                # Extraer de otras entradas
                                for other_item in origin_data.get('localStorage', []):
                                    if 'client_id' in other_item.get('name', '').lower():
                                        client_id = other_item.get('value', '')
                                        break
                            
                            from token_refresher import save_refresh_token
                            save_refresh_token(
                                refresh_token=token_data['secret'],
                                client_id=client_id,
                                scopes=token_data.get('target', '')
                            )
                            logger.info(f"Refresh token extraído y guardado. Válido por ~90 días.")
                            return
                    except (json.JSONDecodeError, KeyError):
                        continue
        
        logger.warning("No se encontró refresh token en el storage state.")
    except Exception as e:
        logger.warning(f"Error extrayendo refresh token: {e}")
        return None

if __name__ == "__main__":
    async def test():
        token = await extract_ms_token()
        print(f"Token final: {token[:20]}..." if token else "Fallo crítico al extraer.")
    asyncio.run(test())
