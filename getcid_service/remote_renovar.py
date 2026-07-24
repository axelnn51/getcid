"""
🔑 RENOVAR TOKEN - Script automatizado para GetCID
====================================================
Ejecuta esto cuando el token expire. Solo necesitas resolver el CAPTCHA.

¿Qué hace?
1. Abre Chrome real (no headless)
2. Navega a visualsupport.microsoft.com
3. Inicia sesión automáticamente con tu cuenta
4. TÚ solo resuelves el CAPTCHA si aparece
5. Captura el refresh token automáticamente
6. Lo envía al servidor Docker via la API
7. ¡Listo! El servidor se auto-renueva por 24h

Uso: python renovar_token.py
"""

import asyncio
import json
import re
import time
import os
import sys
import urllib.parse
import httpx
from main import captcha_event, captcha_clicks

async def validate_token(token: str) -> bool:
    """Valida que un access token realmente funcione contra la API de Microsoft usando DPoP."""
    try:
        from core import process_iid
        # Un IID falso pero con el formato correcto
        res = await process_iid("000000000000000000000000000000000000000000000000000000", token)
        
        # Si Microsoft rechaza el token (403), core.py devuelve "Token expirado o Denegado."
        if not res.get("success") and ("Token expirado" in res.get("error", "") or "403" in res.get("error", "")):
            print(f"   ❌ Token INVÁLIDO (403 Forbidden)")
            return False
            
        # Si Microsoft devuelve error de IID inválido (o cualquier otra cosa que no sea 403), el token es válido
        print(f"   ✅ Token VALIDADO (Respuesta de MS: {res.get('error')})")
        return True
    except Exception as e:
        print(f"   ⚠️ Error validando token: {e}")
        return False

def update_winrate(success):
    stats_file = "captcha_stats.json"
    try:
        stats = {"success": 0, "fail": 0}
        if os.path.exists(stats_file):
            with open(stats_file, "r") as f:
                stats = json.load(f)
                
        if success:
            stats["success"] += 1
        else:
            stats["fail"] += 1
            
        with open(stats_file, "w") as f:
            json.dump(stats, f)
            
        total = stats["success"] + stats["fail"]
        rate = (stats["success"] / total) * 100 if total > 0 else 0
        return f"{stats['success']} aciertos / {stats['fail']} fallos ({rate:.1f}%)"
    except Exception:
        return "N/A"

# ─── Configuración ───
# Datos de la cuenta (se leen del .env)
MS_EMAIL = os.getenv("MS_EMAIL") or "axelnn52@outlook.com"
MS_PASSWORD = os.getenv("MS_PASSWORD") or "@Dotita123"

# URL del servidor Docker (cambiar si es diferente)
GETCID_SERVER = os.getenv("GETCID_SERVER") or "http://localhost:8000"

# Telegram Bot para notificar directo
BOT_TOKEN = os.getenv("BOT_TOKEN") or "8334632533:AAEMCDWK-4sMpmDSSquc5Afz6FRVZjrs6go"
ADMIN_CHAT_ID = os.getenv("ADMIN_IDS") or "7233007906"

# Client ID del SPA de VisualSupport (el que funciona con cuentas personales)
SPA_CLIENT_ID = "2b217cec-607d-4eb6-887e-c928520a14f6"


def print_banner():
    print("\n" + "=" * 65)
    print("  🔑 GETCID - Renovador Automático de Token")
    print("  📋 Solo resuelve el CAPTCHA si aparece. Todo lo demás es automático.")
    print("=" * 65 + "\n")


# ─── Estado de captura: clase local para evitar contaminación entre ejecuciones ───
class _CaptureState:
    """Contenedor de estado aislado por ejecución. Evita globals que persisten entre intentos."""
    def __init__(self):
        self.token = None
        self.refresh_token = None
        self.client_id = None
        self.token_capture_time = None

# Referencia global solo para compatibilidad con on_request (closure)
_state = _CaptureState()


def _kill_orphan_chrome():
    """Mata TODOS los procesos de Chrome/Chromium huérfanos antes de abrir uno nuevo.
    Previene acumulación de procesos zombie que causan OOM y degradación del servidor."""
    import subprocess
    killed = 0
    for proc_name in ['chrome', 'chromium', 'chrome_crashpad']:
        try:
            result = subprocess.run(
                ['pkill', '-9', '-f', proc_name],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                killed += 1
        except Exception:
            pass
    # Limpiar perfiles temporales abandonados
    import glob
    base_dir = "/app/persist" if os.path.isdir("/app/persist") else "."
    for old_profile in glob.glob(os.path.join(base_dir, "getcid_chrome_*")):
        try:
            import shutil
            shutil.rmtree(old_profile, ignore_errors=True)
        except Exception:
            pass
    if killed:
        print(f"🧹 Limpiados {killed} grupos de procesos Chrome huérfanos.")
    return killed

async def run():
    global _state
    _state = _CaptureState()  # Estado LIMPIO cada ejecución
    
    previous_token = None
    try:
        _persist = "/app/persist" if os.path.isdir("/app/persist") else "."
        _token_file = os.path.join(_persist, "ms_token.json")
        if os.path.exists(_token_file):
            with open(_token_file, "r") as f:
                data = json.load(f)
                previous_token = data.get("token")
    except:
        pass
    
    print_banner()

    # ─── ANTI-ZOMBIE: Matar Chrome huérfano de runs anteriores ───
    _kill_orphan_chrome()

    # Verificar que Playwright esté instalado
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("❌ Playwright no está instalado.")
        print("   Ejecuta: pip install playwright && playwright install chromium")
        sys.exit(1)

    try:
        from playwright_stealth import Stealth
        has_stealth = True
    except ImportError:
        has_stealth = False
        print("⚠️ playwright-stealth no instalado. Continuando sin stealth...")

    print(f"📧 Cuenta: {MS_EMAIL}")
    print(f"🌐 Servidor: {GETCID_SERVER}")
    print(f"📱 Telegram: Chat ID {ADMIN_CHAT_ID}\n")

    # ─── VARIABLE PARA CLEANUP EN finally ───
    context = None
    profile_dir = None
    import tempfile
    import shutil

    try:
        async with async_playwright() as p:
            # ─── PERFIL LIMPIO: crear directorio temporal para cada ejecución ───
            # Reutilizar chrome_profile acumula basura (Login Data, Service Workers, 
            # IndexedDB) que interfiere con Arkose Labs y provoca "Something went wrong".
            base_dir = "/app/persist" if os.path.exists("/app/persist") else os.path.dirname(__file__)
            profile_dir = tempfile.mkdtemp(prefix="getcid_chrome_", dir=base_dir)
            print(f"🚀 Abriendo Chrome con perfil LIMPIO temporal: {os.path.basename(profile_dir)}")
            print(f"🖥️  DEBUG DISPLAY: {os.environ.get('DISPLAY')}")

            context = await p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=False,
                args=[
                    '--no-sandbox',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--disable-extensions',
                    '--disable-component-extensions-with-background-pages',
                    '--disable-background-networking',
                    '--disable-sync',
                ],
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900}
            )
            print("   ✅ Chrome lanzado con perfil limpio (sin basura acumulada).")
        
            # En contextos persistentes, la primera página ya viene abierta
            page = context.pages[0] if context.pages else await context.new_page()

            # Stealth para evadir detección
            if has_stealth:
                stealth = Stealth()
                await stealth.apply_stealth_async(page)

            # ─── Interceptar el Bearer token de las requests ───
            _warned_duplicate = False
            async def on_request(request):
                nonlocal _warned_duplicate
                if "api/productActivation" in request.url or "visualsupport.microsoft.com/api/" in request.url:
                    auth = request.headers.get("authorization", "")
                    if "Bearer" in auth or "DPoP" in auth:
                        token = auth.replace("Bearer ", "").replace("DPoP ", "").strip()
                        # Ignorar si es exactamente el mismo token que ya teníamos (falso positivo de cache)
                        if previous_token and token == previous_token:
                            if not _warned_duplicate:
                                print(f"\n⚠️ Token interceptado es IDÉNTICO al anterior. Ignorando falso positivo.")
                                _warned_duplicate = True
                        else:
                            _state.token = token
                            _state.token_capture_time = time.time()
                            print(f"\n🎯 ¡ACCESS TOKEN CAPTURADO! ({len(token)} chars)")

            page.on("request", on_request)

            # ─── PASO 1: Navegar a VisualSupport ───
            print("🌐 Navegando a visualsupport.microsoft.com...")
            await page.goto("https://visualsupport.microsoft.com/", wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            # ─── PASO 2 y 3: Bucle continuo de automatización y espera ───
            print("\n" + "=" * 65)
            print("  🤖 AUTOMATIZACIÓN ACTIVA")
            print("  El script está manejando el login. Si ves un CAPTCHA, resuélvelo.")
            print("=" * 65 + "\n")

            start_wait = time.time()
            max_wait = 360  # 6 minutos (suficiente para agotar todos los intentos de IA antes de pedir ayuda)

            # Calcular intentos máximos de IA: 3 intentos por cada API key configurada
            gemini_keys_raw = os.getenv("GEMINI_API_KEY") or ""
            num_api_keys = len([k for k in gemini_keys_raw.split(",") if k.strip()]) or 1
            ai_enabled = (os.getenv("AI_SOLVER_ENABLED") or "").strip().lower() == "true"
            max_ai_attempts = num_api_keys * 3  # Vuelve a 12 (ej: 4 keys * 3)
            ai_fail_count = 0
            last_ai_clicks = -1
            print(f"  🤖 IA {'ACTIVADA' if ai_enabled else 'DESACTIVADA'}")
            print(f"  🔑 API Keys detectadas: {num_api_keys} × 3 intentos = {max_ai_attempts} intentos máx")
            print(f"  📝 ENV DEBUG: AI_SOLVER_ENABLED='{os.getenv('AI_SOLVER_ENABLED')}' | GEMINI_API_KEY={len(gemini_keys_raw)} chars\n")

            MIN_CAPTURE_WAIT = 5  # Mínimo 5 segundos antes de aceptar un token (evitar cache viejo)
            reload_challenge_count = 0  # Contador de "Something went wrong" / "Reload Challenge"
            MAX_RELOAD_ATTEMPTS = 3  # Máximo de reloads antes de abortar esta ejecución

            while time.time() - start_wait < max_wait:
                elapsed = int(time.time() - start_wait)

                # ¿Ya tenemos el token interceptado por red?
                if _state.token and elapsed >= MIN_CAPTURE_WAIT:
                    print(f"\n✅ Token capturado en red después de {elapsed}s")
                    if ai_enabled and last_ai_clicks != -1 and ai_fail_count < max_ai_attempts:
                        print("📊 Registrando victoria para la IA...")
                        update_winrate(True)
                
                    # ── CAPTURA ANTICIPADA DEL REFRESH TOKEN ──
                    if not _state.refresh_token:
                        print("🔍 Extrayendo refresh token antes de salir del loop...")
                        await page.wait_for_timeout(2000)
                        for _sn, _js in [("sessionStorage", "window.sessionStorage"), ("localStorage", "window.localStorage")]:
                            try:
                                _raw = await page.evaluate(f"() => JSON.stringify({_js})")
                                if not _raw:
                                    continue
                                _storage = json.loads(_raw)
                                for _k, _v in _storage.items():
                                    try:
                                        _parsed = json.loads(_v)
                                        if isinstance(_parsed, dict) and "refreshtoken" in _k.lower() and "secret" in _parsed and not _state.refresh_token:
                                            _state.refresh_token = _parsed["secret"]
                                            _state.client_id = _parsed.get("client_id", SPA_CLIENT_ID)
                                            print(f"   🎯 REFRESH TOKEN (anticipado) de {_sn}! ({len(_state.refresh_token)} chars)")
                                    except Exception:
                                        continue
                            except Exception as _e:
                                print(f"   ⚠️ No se pudo leer {_sn} anticipado: {_e}")
                
                    break
                elif _state.token and elapsed < MIN_CAPTURE_WAIT:
                    # Token capturado demasiado rápido → probablemente del cache viejo
                    print(f"  ⚠️ Token capturado en {elapsed}s (< {MIN_CAPTURE_WAIT}s). Podría ser cache viejo, esperando más...")
                    _state.token = None
                    _state.token_capture_time = None
                
                # ¿Llegamos a la página final de bienvenida?
                if "visualsupport.microsoft.com/welcome" in page.url:
                    print("\n✅ Llegamos a la página de bienvenida. Extrayendo tokens...")
                    if ai_enabled and last_ai_clicks != -1 and ai_fail_count < max_ai_attempts:
                        print("📊 Registrando victoria para la IA...")
                        update_winrate(True)
                    break

                # ¿Ya terminó el login y el token está en la memoria del navegador?
                if elapsed > 0 and elapsed % 3 == 0:
                    try:
                        login_complete = False
                        for js_code in ["window.sessionStorage", "window.localStorage"]:
                            raw = await page.evaluate(f"() => JSON.stringify({js_code})")
                            if raw and "accesstoken" in raw.lower() and "secret" in raw.lower():
                                # Verificar que NO sea el mismo token viejo
                                try:
                                    _storage_check = json.loads(raw)
                                    for _ck, _cv in _storage_check.items():
                                        if "accesstoken" in _ck.lower():
                                            _cp = json.loads(_cv)
                                            if isinstance(_cp, dict) and "secret" in _cp:
                                                if previous_token and _cp["secret"] == previous_token:
                                                    continue  # Token viejo, seguir buscando
                                                login_complete = True
                                                break
                                except Exception:
                                    login_complete = True  # En caso de error, asumir que es nuevo
                                break
                        if login_complete:
                            print(f"\n✅ Login completado exitosamente. Extrayendo tokens del storage...")
                            if ai_enabled and last_ai_clicks != -1 and ai_fail_count < max_ai_attempts:
                                print("📊 Registrando victoria para la IA...")
                                update_winrate(True)
                            break
                    except Exception:
                        pass

                try:
                    # 0. DETECCIÓN DE BLOQUEO DE CUENTA ("You've tried to sign in too many times")
                    lockout_msg = page.get_by_text(re.compile("tried to sign in too many times|demasiados intentos|too many times|account.*locked|cuenta.*bloqueada", re.IGNORECASE))
                    if await lockout_msg.count() > 0:
                        if await lockout_msg.first.is_visible(timeout=100):
                            print("\n🚫 ¡CUENTA BLOQUEADA POR MICROSOFT! Abortando renovación.")
                            try:
                                async with httpx.AsyncClient(timeout=10) as http:
                                    await http.post(
                                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                                        json={"chat_id": ADMIN_CHAT_ID, "text": "🚫 *Renovación Abortada*\n\nMicrosoft bloqueó la cuenta por demasiados intentos de login.\nEspera 30-60 minutos y usa /deviceauth como alternativa.", "parse_mode": "Markdown"}
                                    )
                            except: pass
                            await context.close()
                            return

                    # 0.1 DETECCIÓN DE "Too Many Requests" / Rate Limiting
                    rate_limit_msg = page.get_by_text(re.compile("Too Many Requests|rate limit|demasiadas solicitudes", re.IGNORECASE))
                    if await rate_limit_msg.count() > 0:
                        if await rate_limit_msg.first.is_visible(timeout=100):
                            print("\n🚫 ¡RATE LIMITED POR MICROSOFT! Borrando caché para reintentar.")
                            try:
                                # Borrar caché guardado para no arrastrar el bloqueo
                                state_dir = os.path.join(base_dir, "states")
                                state_file = os.path.join(state_dir, "state_renovar.json")
                                if os.path.exists(state_file):
                                    os.remove(state_file)
                                    print("🗑️ Caché de sesión eliminado exitosamente.")
                                
                                async with httpx.AsyncClient(timeout=10) as http:
                                    await http.post(
                                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                                        json={"chat_id": ADMIN_CHAT_ID, "text": "⚠️ *Rate Limited Detectado*\n\nSe borró el caché de sesión. Reintentando de forma limpia...", "parse_mode": "Markdown"}
                                    )
                            except Exception as e: 
                                print(f"Error limpiando caché: {e}")
                            await context.close()
                            return False # Devuelve False para que main.py lo vuelva a intentar en el siguiente ciclo

                    # 0.2 Error de Microsoft ("Our services aren't available right now" o 504 Gateway Timeout) -> Recargar
                    error_msg = page.get_by_text(re.compile("services aren't available right now|servicios no están disponibles|Azure Front Door|Gateway Timeout", re.IGNORECASE))
                    if await error_msg.count() > 0:
                        if await error_msg.first.is_visible(timeout=100):
                            print("🔄 Microsoft dio error 500/504 (Azure Front Door). Recargando la página automáticamente...")
                            await page.reload()
                            await page.wait_for_timeout(3000)
                            continue

                    # 0.2.5 Error de Contraseña ("Password sign-in isn't available" o "Try another method")
                    pwd_err_msg = page.get_by_text(re.compile("Password sign-in isn't available|Contraseña incorrecta", re.IGNORECASE))
                    if await pwd_err_msg.count() > 0 and await pwd_err_msg.first.is_visible(timeout=100):
                        print(f"\n🚫 ¡ERROR DE CONTRASEÑA EN MICROSOFT! (La cuenta {MS_EMAIL} requiere código o está mal la clave). Borrando caché y saltando...")
                        try:
                            async with httpx.AsyncClient(timeout=10) as http:
                                await http.post(
                                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                                    json={"chat_id": ADMIN_CHAT_ID, "text": f"🚫 *Login Fallido*\n\nLa cuenta `{MS_EMAIL}` rechazó el inicio de sesión con contraseña.\nPasando a la siguiente...", "parse_mode": "Markdown"}
                                )
                            # Borrar caché guardado para no arrastrar el bloqueo
                            state_dir = os.path.join(base_dir, "states")
                            state_file = os.path.join(state_dir, "state_renovar.json")
                            if os.path.exists(state_file):
                                os.remove(state_file)
                        except: pass
                        await context.close()
                        return False

                    # 0.3 🔴 FIX CRÍTICO: "Something went wrong. Please reload the challenge"
                    # Esta pantalla de Arkose Labs NO se detectaba → causaba loops infinitos de screenshots
                    for _frame in page.frames:
                        try:
                            _sww = _frame.get_by_text(re.compile("Something went wrong|Algo salió mal|reload the challenge|recargar el desafío", re.IGNORECASE))
                            if await _sww.count() > 0 and await _sww.first.is_visible(timeout=200):
                                reload_challenge_count += 1
                                print(f"\n🔴 'Something went wrong' detectado (intento {reload_challenge_count}/{MAX_RELOAD_ATTEMPTS})")
                            
                                if reload_challenge_count > MAX_RELOAD_ATTEMPTS:
                                    print(f"   ❌ Máximo de reloads alcanzado ({MAX_RELOAD_ATTEMPTS}). Abortando esta ejecución.")
                                    try:
                                        async with httpx.AsyncClient(timeout=10) as http:
                                            await http.post(
                                                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                                                json={"chat_id": ADMIN_CHAT_ID, "text": f"🔴 *Arkose Labs bloqueó el CAPTCHA*\n\n'Something went wrong' apareció {reload_challenge_count} veces.\nAbortando este intento. Se reintentará en 2 min.", "parse_mode": "Markdown"}
                                            )
                                    except: pass
                                    await context.close()
                                    # Limpiar perfil temporal
                                    try:
                                        shutil.rmtree(profile_dir, ignore_errors=True)
                                    except: pass
                                    return False
                            
                                # Intentar click en "Reload Challenge"
                                _reload_btn = _frame.get_by_role("button", name=re.compile("Reload|Recargar", re.IGNORECASE))
                                if await _reload_btn.count() > 0:
                                    print("   🔄 Clickeando 'Reload Challenge'...")
                                    await _reload_btn.first.click()
                                    await page.wait_for_timeout(3000)
                                else:
                                    # Si no hay botón, recargar la página completa
                                    print("   🔄 No se encontró botón. Recargando página completa...")
                                    await page.goto("https://visualsupport.microsoft.com/", wait_until="domcontentloaded")
                                    await page.wait_for_timeout(3000)
                                break  # Salir del loop de frames
                        except Exception:
                            continue

                    # 0.5. Botón "Start" del CAPTCHA (Arkose Labs usa iframes anidados)
                    try:
                        clicked_captcha = False
                        for frame in page.frames:
                            try:
                                # 1. Ver si ya estamos adentro del puzzle (flechas visibles)
                                right_arrow = frame.locator("a.navigate.right, button.navigate-right, [aria-label*='next image' i], [aria-label*='siguiente' i], a[aria-label*='right' i]")
                                if await right_arrow.count() > 0 and await right_arrow.first.is_visible(timeout=500):
                                    print("🚨 CAPTCHA de flechas detectado.")
                                
                                    # Tomar screenshot del iframe completo
                                    body = frame.locator("body")
                                    await body.screenshot(path="captcha.png")
                                
                                    clicks = -1
                                    # Intentar con IA primero (PLAN A) si no ha agotado todos los intentos
                                    if ai_enabled and ai_fail_count < max_ai_attempts:
                                        print(f"[{time.strftime('%H:%M:%S')}] 🤖 IA Activada: Analizando puzzle con Gemini Vision... (Intento {ai_fail_count+1}/{max_ai_attempts})")
                                        try:
                                            from ai_solver import resolver_captcha_con_ia
                                        except ImportError as ie:
                                            print(f"[{time.strftime('%H:%M:%S')}] ❌ ERROR IMPORTANDO ai_solver: {ie}")
                                            resolver_captcha_con_ia = None
                                    
                                        if resolver_captcha_con_ia:
                                            # Tomar tiempo antes
                                            start_time = time.time()
                                            try:
                                                clicks = resolver_captcha_con_ia("captcha.png")
                                            except Exception as ai_err:
                                                print(f"[{time.strftime('%H:%M:%S')}] ❌ ERROR EJECUTANDO ai_solver: {ai_err}")
                                                clicks = -1
                                            elapsed = time.time() - start_time
                                        
                                            if 0 <= clicks <= 5:
                                                print(f"[{time.strftime('%H:%M:%S')}] ✅ La IA determinó que son {clicks} clics en {elapsed:.1f} segundos.")
                                                last_ai_clicks = clicks
                                            else:
                                                import random
                                                clicks = random.randint(0, 5)
                                                print(f"[{time.strftime('%H:%M:%S')}] 🎲 La IA falló o está sin cuota. Adivinando al azar: {clicks} clics.")
                                                last_ai_clicks = clicks

                                    # Si la IA falló, pedir ayuda humana (PLAN B)
                                    if clicks == -1:
                                        print(f"[{time.strftime('%H:%M:%S')}] 🚨 Solicitando ayuda por Telegram (Plan B)...")
                                    
                                        ai_info = ""
                                        if last_ai_clicks != -1:
                                            winrate = update_winrate(False)
                                            raw_reasoning = "N/A"
                                            try:
                                                with open("last_reasoning.txt", "r", encoding="utf-8") as f:
                                                    raw_reasoning = f.read()
                                            except:
                                                pass
                                            ai_info = f"\n\n🤖 *Último intento IA:* {last_ai_clicks} clics (Falló)\n📊 *Winrate IA:* {winrate}\n📝 *Razonamiento RAW:*\n`{raw_reasoning[:500]}...`"
                                        reply_markup = {
                                            "inline_keyboard": [
                                                [
                                                    {"text": "0", "callback_data": "solve_captcha_0"},
                                                    {"text": "1", "callback_data": "solve_captcha_1"},
                                                    {"text": "2", "callback_data": "solve_captcha_2"}
                                                ],
                                                [
                                                    {"text": "3", "callback_data": "solve_captcha_3"},
                                                    {"text": "4", "callback_data": "solve_captcha_4"},
                                                    {"text": "5", "callback_data": "solve_captcha_5"}
                                                ]
                                            ]
                                        }
                                    
                                        async with httpx.AsyncClient() as http:
                                            with open("captcha.png", "rb") as f:
                                                await http.post(
                                                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                                                    data={
                                                        "chat_id": ADMIN_CHAT_ID,
                                                        "caption": f"🚨 *Azure WAF CAPTCHA Detectado*\n¿Cuántos clics a la *DERECHA* necesita el tren?{ai_info}",
                                                        "reply_markup": json.dumps(reply_markup),
                                                        "parse_mode": "Markdown"
                                                    },
                                                    files={"photo": f}
                                                )
                                    
                                        print("⏳ Esperando respuesta del administrador en Telegram (Máximo 5 minutos)...")
                                        import main
                                        main.captcha_event.clear()
                                        try:
                                            await asyncio.wait_for(main.captcha_event.wait(), timeout=300)
                                            clicks = main.captcha_clicks
                                            print(f"👨‍💻 Recibida instrucción manual: {clicks} clics a la derecha.")
                                        except asyncio.TimeoutError:
                                            print("⏱️ Se acabó el tiempo de 5 minutos. Abortando este CAPTCHA.")
                                            clicks = -1
                                            break  # Salir del bucle del CAPTCHA y probar otra cuenta
                                
                                    # Ejecutar los clics con calma para que la animación termine
                                    for i in range(clicks):
                                        await right_arrow.first.click()
                                        print(f"   ▶ Clic {i+1}/{clicks}")
                                        await page.wait_for_timeout(1200) # 1.2 segundos entre clics
                                
                                    # Esperar a que la última animación termine antes de enviar
                                    if clicks > 0:
                                        await page.wait_for_timeout(1000)
                                
                                    # Clic en Submit
                                    submit_btn = frame.locator("button#home_children_button, button[type='submit'], button:has-text('Submit'), button:has-text('Enviar')")
                                    if await submit_btn.count() > 0:
                                        await submit_btn.first.click()
                                        print("✅ Botón Submit clickeado.")
                                        await page.wait_for_timeout(3000)
                                
                                    clicked_captcha = True
                                    break
                            
                                # 2. Detectar si falló ("That was not quite right. You can try again.")
                                try_again_btn = frame.get_by_role("button", name=re.compile("Try again|Intentar de nuevo", re.IGNORECASE))
                                if await try_again_btn.count() > 0 and await try_again_btn.first.is_visible(timeout=100):
                                    ai_fail_count += 1
                                    print(f"[{time.strftime('%H:%M:%S')}] ❌ CAPTCHA incorrecto. Fallo acumulado: {ai_fail_count}/{max_ai_attempts}. Clickeando 'Try again'...")
                                
                                    # Enviar log de fallo a Telegram para entrenamiento
                                    if last_ai_clicks != -1:
                                        try:
                                            raw_reasoning = "N/A"
                                            try:
                                                with open("last_reasoning.txt", "r", encoding="utf-8") as f:
                                                    raw_reasoning = f.read()[:400]
                                            except:
                                                pass
                                            async with httpx.AsyncClient() as http:
                                                with open("captcha.png", "rb") as f:
                                                    await http.post(
                                                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                                                        data={
                                                            "chat_id": ADMIN_CHAT_ID,
                                                            "caption": f"❌ *Fallo IA #{ai_fail_count}/{max_ai_attempts}*\n🤖 Respuesta: *{last_ai_clicks} clics* (Incorrecto)\n\n📝 `{raw_reasoning}...`",
                                                            "parse_mode": "Markdown"
                                                        },
                                                        files={"photo": f}
                                                    )
                                        except:
                                            pass
                                
                                    await try_again_btn.first.click()
                                    await page.wait_for_timeout(2000)
                                    clicked_captcha = True
                                    break
                            
                                # 2. Si no estamos en las flechas, quizá estamos en la pantalla inicial "Start"
                                # Intento A: Como botón real
                                f_btn = frame.get_by_role("button", name=re.compile("^Start$|^Empezar$|^Comenzar$", re.IGNORECASE))
                                if await f_btn.count() > 0 and await f_btn.first.is_visible(timeout=100):
                                    print("🧩 Iniciando CAPTCHA automáticamente (Botón)...")
                                    await f_btn.first.click(timeout=1000)
                                    await page.wait_for_timeout(2000)
                                    clicked_captcha = True
                                    break
                                
                                # Intento B: Como texto simple (div/span disfrazado)
                                f_btn_text = frame.get_by_text(re.compile("^Start$|^Empezar$|^Comenzar$", re.IGNORECASE))
                                if await f_btn_text.count() > 0 and await f_btn_text.first.is_visible(timeout=100):
                                    print("🧩 Iniciando CAPTCHA automáticamente (Texto)...")
                                    await f_btn_text.first.click(timeout=1000)
                                    await page.wait_for_timeout(2000)
                                    clicked_captcha = True
                                    break
                            except Exception as inner_e:
                                continue
                    
                        if clicked_captcha:
                            continue
                    except Exception as e:
                        pass

                    # 1. Botón inicial en VisualSupport (Múltiples variaciones en Inglés/Español)
                    # Si ya estamos en /welcome, NO darle a Get Started para evitar loops
                    if "welcome" not in page.url:
                        start_btn = page.get_by_role("button", name=re.compile("Continuar|Get Started|Proceed", re.IGNORECASE))
                        if await start_btn.count() > 0:
                            if await start_btn.first.is_visible(timeout=100):
                                print("🔘 Botón 'Continuar / Get Started' detectado. Clickeando...")
                                await start_btn.first.click()
                                await page.wait_for_timeout(2000)
                                continue

                    # 1.5 Pick an account (Seleccionar una cuenta)
                    try:
                        pick_account_title = page.get_by_text(re.compile("Pick an account|Seleccionar una cuenta|Elegir una cuenta", re.IGNORECASE))
                        if await pick_account_title.count() > 0 and await pick_account_title.first.is_visible(timeout=100):
                            account_tile = page.get_by_text(MS_EMAIL, exact=False)
                            if await account_tile.count() > 0 and await account_tile.first.is_visible(timeout=100):
                                print("👤 Seleccionando cuenta guardada...")
                                await account_tile.first.click()
                                await page.wait_for_timeout(2000)
                                continue
                    except:
                        pass

                    # 2. Input de Email
                    email_input = page.locator("input[type='email'], input[name='loginfmt']")
                    if await email_input.count() > 0:
                        if await email_input.first.is_visible(timeout=100):
                            current_val = await email_input.first.input_value()
                            if not current_val:  # Solo rellenar si está vacío
                                print(f"📝 Rellenando email: {MS_EMAIL}")
                                await email_input.first.fill(MS_EMAIL)
                                await page.wait_for_timeout(500)
                        
                            try:
                                await page.locator("input[type='submit']").first.click(timeout=1000)
                            except:
                                await page.keyboard.press("Enter")
                            print("   ✅ Siguiente clickeado.")
                            await page.wait_for_timeout(2000)
                            continue

                    # 2.5 "Use your password" / "Usar su contraseña" (Si pide código de verificación)
                    use_pwd_btn = page.get_by_text(re.compile("Use your password|Usar su contraseña", re.IGNORECASE))
                    if await use_pwd_btn.count() > 0:
                        if await use_pwd_btn.first.is_visible(timeout=100):
                            print("🔄 Microsoft pidió código. Clickeando en 'Usar contraseña'...")
                            await use_pwd_btn.first.click()
                            await page.wait_for_timeout(2000)
                            continue

                    # 3. Input de Contraseña
                    pwd_input = page.locator("input[type='password'], input[name='passwd']")
                    if await pwd_input.count() > 0:
                        if await pwd_input.first.is_visible(timeout=100):
                            current_val = await pwd_input.first.input_value()
                            if not current_val:  # Solo rellenar si está vacío
                                print("🔑 Rellenando contraseña...")
                                await pwd_input.first.fill(MS_PASSWORD)
                                await page.wait_for_timeout(500)
                                try:
                                    await page.locator("input[type='submit']").first.click(timeout=1000)
                                except:
                                    await page.keyboard.press("Enter")
                                print("   ✅ Iniciar sesión clickeado.")
                                await page.wait_for_timeout(2000)
                                continue

                    # 4. "¿Mantener la sesión iniciada?" (Botón "Yes" suele tener id="idBtn_Accept" o texto "Yes"/"Sí")
                    yes_btn = page.locator("input[id='idBtn_Accept'], button[id='idBtn_Accept']")
                    if await yes_btn.count() == 0:
                        yes_btn = page.get_by_role("button", name=re.compile("^Yes$|^Sí$|^Si$", re.IGNORECASE))
                    
                    if await yes_btn.count() > 0:
                        if await yes_btn.first.is_visible(timeout=100):
                            print("✅ 'Mantener sesión iniciada' (Sí) clickeado. (Brazalete extendido a 90 días)")
                            await yes_btn.first.click()
                            await page.wait_for_timeout(2000)
                            continue

                except Exception as e:
                    # Silenciar errores del bucle para no ensuciar la consola
                    pass

                if elapsed % 10 == 0 and elapsed > 0:
                    print(f"  ⏳ Esperando... ({elapsed}s)")
                
                    # ─── DEBUG: Enviar screenshot a Telegram si está atascado por mucho tiempo ───
                    if elapsed % 30 == 0:
                        try:
                            print(f"  📸 Enviando screenshot de debug a Telegram...")
                            await page.screenshot(path="debug_stuck.png")
                            async with httpx.AsyncClient() as http:
                                with open("debug_stuck.png", "rb") as f:
                                    await http.post(
                                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                                        data={
                                            "chat_id": ADMIN_CHAT_ID,
                                            "caption": f"🤖 *DEBUG* ({elapsed}s):\nSigo esperando en esta pantalla. ¿Requiere acción manual?",
                                            "parse_mode": "Markdown"
                                        },
                                        files={"photo": f}
                                    )
                        except Exception as e:
                            print(f"  ⚠️ Error enviando debug screenshot: {e}")

                # Esperar un poco antes de volver a verificar el DOM
                await page.wait_for_timeout(1000)

            # ─── PASO 4: Extraer tokens del Storage (MSAL cache) ───
            if not _state.token:
                print("\n⚠️ No se capturó token del interceptor. Buscando en storage...")
            else:
                print("\n🔍 Buscando refresh token en storage del navegador...")

            # MSAL.js guarda tokens en sessionStorage/localStorage con claves como:
            #   {homeAccountId}-{env}-accesstoken-{clientId}-{realm}-{scopes}
            #   {homeAccountId}-{env}-refreshtoken-{clientId}--
            for storage_name, js_code in [("sessionStorage", "window.sessionStorage"), ("localStorage", "window.localStorage")]:
                try:
                    raw = await page.evaluate(f"() => JSON.stringify({js_code})")
                    if not raw:
                        continue
                    storage = json.loads(raw)
                    print(f"\n📦 {storage_name}: {len(storage)} entries")

                    for key, value in storage.items():
                        key_lower = key.lower()
                        try:
                            parsed = json.loads(value)
                            if not isinstance(parsed, dict):
                                continue

                            # ── Refresh Token ──
                            if "refreshtoken" in key_lower and "secret" in parsed and not _state.refresh_token:
                                _state.refresh_token = parsed["secret"]
                                _state.client_id = parsed.get("client_id", SPA_CLIENT_ID)
                                print(f"   🎯 REFRESH TOKEN extraído de {storage_name}! ({len(_state.refresh_token)} chars)")

                            # ── Access Token (validar que no sea el viejo) ──
                            if "accesstoken" in key_lower and "secret" in parsed and not _state.token:
                                _candidate = parsed["secret"]
                                if previous_token and _candidate == previous_token:
                                    print(f"   ⚠️ Access token en {storage_name} es IDÉNTICO al anterior. Descartando.")
                                else:
                                    _state.token = _candidate
                                    _state.client_id = parsed.get("client_id", SPA_CLIENT_ID)
                                    print(f"   🎯 ACCESS TOKEN extraído de {storage_name}! ({len(_state.token)} chars)")

                        except (json.JSONDecodeError, KeyError, TypeError):
                            continue

                    # ── Búsqueda amplia: cualquier valor que parezca un refresh token ──
                    if not _state.refresh_token:
                        for key, value in storage.items():
                            if isinstance(value, str) and len(value) > 100 and "refresh" in key.lower():
                                if not value.startswith("{"):
                                    _state.refresh_token = value
                                    print(f"   🎯 REFRESH TOKEN (raw) de {storage_name}! ({len(value)} chars)")
                                    break

                except Exception as e:
                    print(f"   ⚠️ Error leyendo {storage_name}: {e}")

            # Guardar storage state (las cookies de login) para referencia
            try:
                state_dir = os.path.join(base_dir, "states")
                os.makedirs(state_dir, exist_ok=True)
                state_file = os.path.join(state_dir, "state_renovar.json")
                await context.storage_state(path=state_file)
                print("💾 Estado de sesión guardado.")
            except:
                pass

            await context.close()
        
            # Limpiar perfil temporal para no acumular basura en disco
            try:
                shutil.rmtree(profile_dir, ignore_errors=True)
                print("🧹 Perfil temporal limpiado.")
            except:
                pass

        # ─── Copiar estado a variables locales para compatibilidad con el resto del código ───
        captured_token = _state.token
        captured_refresh_token = _state.refresh_token
        captured_client_id = _state.client_id
    
        # ─── PASO 5: Validar tokens antes de reportar ───
        print("\n" + "=" * 65)

        if not captured_token and not captured_refresh_token:
            print("  ❌ NO SE CAPTURÓ NINGÚN TOKEN")
            print("  Posibles causas:")
            print("  • No completaste el login / CAPTCHA")
            print("  • La página no cargó correctamente")
            print("  • Microsoft bloqueó la sesión")
            print("=" * 65)
            # Notificar fallo a Telegram
            try:
                async with httpx.AsyncClient(timeout=10) as http:
                    await http.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                        json={"chat_id": ADMIN_CHAT_ID, "text": "❌ *Renovación Fallida*\nNo se capturó ningún token. Revisa el servidor.", "parse_mode": "Markdown"}
                    )
            except: pass
            return False  # No usar sys.exit() porque mataría el servidor FastAPI si se ejecuta desde el cron

        # ─── VALIDACIÓN: Verificar que el token REALMENTE funcione ───
        if captured_token:
            print("\n🔍 Validando access token contra Microsoft API...")
            token_valid = await validate_token(captured_token)
            if not token_valid:
                print("  ❌ TOKEN CAPTURADO ES INVÁLIDO. Descartando.")
                captured_token = None
                # Intentar usar el refresh token para obtener uno nuevo
                if captured_refresh_token:
                    print("  🔄 Intentando generar access token desde refresh token...")
                    try:
                        import token_refresher
                        token_refresher.save_refresh_token(captured_refresh_token, captured_client_id or SPA_CLIENT_ID, "")
                        new_token = await token_refresher.refresh_access_token()
                        if new_token:
                            captured_token = new_token
                            print("  ✅ Nuevo access token generado desde refresh token!")
                            token_valid = await validate_token(captured_token)
                            if not token_valid:
                                print("  ❌ Incluso el nuevo token es inválido.")
                                captured_token = None
                    except Exception as e:
                        print(f"  ⚠️ Error generando token desde refresh: {e}")
    
        if not captured_token:
            print("  ❌ NO HAY TOKEN VÁLIDO DESPUÉS DE LA VALIDACIÓN")
            print("=" * 65)
            try:
                async with httpx.AsyncClient(timeout=10) as http:
                    await http.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                        json={"chat_id": ADMIN_CHAT_ID, "text": "❌ *Renovación Fallida*\nToken capturado pero es inválido (403). Se necesita re-login manual.", "parse_mode": "Markdown"}
                    )
            except: pass
            return

        print("  🎉 ¡TOKENS CAPTURADOS Y VALIDADOS!")
        print(f"  🔑 Access Token: ✅ VALIDADO")
        print(f"  🔄 Refresh Token: {'✅ SÍ' if captured_refresh_token else '❌ NO'}")
        print(f"  🆔 Client ID: {captured_client_id or SPA_CLIENT_ID}")
        print("=" * 65)

        client_id = captured_client_id or SPA_CLIENT_ID
        success = False

        # ─── PASO 6: Guardar Tokens Directamente ───
        if captured_refresh_token:
            try:
                import token_refresher
                token_refresher.save_refresh_token(captured_refresh_token, client_id, "")
                print("💾 Refresh token guardado localmente.")
            except Exception as e:
                print(f"⚠️ Error guardando refresh token: {e}")

        # ─── PASO 6.5: Resetear alertas de expiración ───
        try:
            import token_refresher
            token_refresher.reset_expiration_alerts()
            print("🔄 Alertas de expiración reseteadas.")
        except Exception as e:
            print(f"⚠️ Error reseteando alertas: {e}")

        # ─── PASO 7: Actualizar Access Token en Memoria ───
        for server_url in [GETCID_SERVER, "http://localhost:8000"]:
            try:
                async with httpx.AsyncClient(timeout=10) as http:
                    if captured_token:
                        resp = await http.post(
                            f"{server_url}/api/settoken",
                            json={"token": captured_token, "duration": 3500, "is_playwright": True}
                        )
                        if resp.json().get("success"):
                            print(f"✅ Access token actualizado en {server_url}")
                            success = True
                            break
            except:
                pass

        # ─── PASO 8: Notificar a Telegram ───
        if captured_token:
            try:
                async with httpx.AsyncClient(timeout=30) as http:
                    # Obtener winrate actual
                    winrate_str = ""
                    try:
                        if os.path.exists("captcha_stats.json"):
                            with open("captcha_stats.json", "r") as f:
                                stats = json.load(f)
                            total = stats["success"] + stats["fail"]
                            rate = (stats["success"] / total) * 100 if total > 0 else 0
                            winrate_str = f"\n📊 *Winrate IA:* {stats['success']}/{total} ({rate:.0f}%)"
                    except:
                        pass
                
                    await http.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                        json={
                            "chat_id": ADMIN_CHAT_ID,
                            "text": (
                                "✅ *Token Renovado y Validado*\n\n"
                                f"🔑 Access Token: ✅ (`{captured_token[:15]}...{captured_token[-5:]}`)\n"
                                f"🔄 Refresh Token: {'✅ Guardado' if captured_refresh_token else '❌ (SPA no lo expone)'}\n"
                                f"📅 Próxima renovación: medianoche"
                                f"{winrate_str}"
                            ),
                            "parse_mode": "Markdown"
                        }
                    )
                    print("✅ Mensaje de éxito enviado a Telegram!")
            except Exception as e:
                print(f"⚠️ Error enviando éxito a Telegram: {e}")

        print("\n" + "=" * 65)
        if success or captured_refresh_token:
            print("  🎉 ¡TODO LISTO! El sistema ya tiene los nuevos tokens.")
            return True
        else:
            print("  ⚠️ Token capturado pero hubo problemas al guardarlo.")
            return False

    except Exception as e:
        # ─── CATCH-ALL: cualquier error no capturado ───
        print(f"\n💥 ERROR CRÍTICO EN PLAYWRIGHT: {e}")
        import traceback
        traceback.print_exc()
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                await http.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={"chat_id": ADMIN_CHAT_ID, "text": f"💥 *Error Crítico en Playwright*\n\n`{str(e)[:200]}`\n\nEl sistema se recuperará automáticamente.", "parse_mode": "Markdown"}
                )
        except:
            pass
        return False

    finally:
        # ─── LIMPIEZA GARANTIZADA: se ejecuta SIEMPRE, incluso en crash ───
        print("\n🧹 [FINALLY] Ejecutando limpieza garantizada...")
        
        # 1. Cerrar contexto si quedó abierto
        if context is not None:
            try:
                await context.close()
                print("   ✅ Contexto de Playwright cerrado.")
            except Exception as e:
                print(f"   ⚠️ Error cerrando contexto: {e}")
        
        # 2. Limpiar perfil temporal
        if profile_dir and os.path.exists(profile_dir):
            try:
                shutil.rmtree(profile_dir, ignore_errors=True)
                print(f"   ✅ Perfil temporal {os.path.basename(profile_dir)} eliminado.")
            except Exception:
                pass
        
        # 3. Matar CUALQUIER proceso Chrome que haya quedado vivo
        _kill_orphan_chrome()
        print("🧹 [FINALLY] Limpieza completada.\n")


async def safe_run():
    """Wrapper anti-caídas: ejecuta run() con timeout global de 5 minutos.
    Si Playwright se queda colgado (CAPTCHA, página lenta, etc.), se mata forzosamente.
    """
    MAX_RUN_TIMEOUT = 300  # 5 minutos máximo por ejecución completa
    
    try:
        result = await asyncio.wait_for(run(), timeout=MAX_RUN_TIMEOUT)
        return result
    except asyncio.TimeoutError:
        print(f"\n⏱️ TIMEOUT GLOBAL: run() tardó más de {MAX_RUN_TIMEOUT}s. Matando Chrome forzosamente...")
        _kill_orphan_chrome()
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                await http.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={"chat_id": ADMIN_CHAT_ID, "text": f"⏱️ *Timeout en Playwright ({MAX_RUN_TIMEOUT}s)*\n\nEl proceso se mató automáticamente. Se reintentará.", "parse_mode": "Markdown"}
                )
        except:
            pass
        return False
    except Exception as e:
        print(f"\n💥 ERROR EN safe_run: {e}")
        _kill_orphan_chrome()
        return False


if __name__ == "__main__":
    import sys
    success = asyncio.run(safe_run())
    if not success:
        sys.exit(1)

