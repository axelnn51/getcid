"""
Módulo de resolución automática de CAPTCHA para GetCID.
Soporta CapMonster Cloud, noCaptchaAi y CapSolver.

IMPORTANTE — CapMonster FunCaptcha:
  • UserAgent DEBE ser exactamente el de docs.capmonster.cloud (Chrome/147).
  • El blob se DEBE interceptar desde el tráfico de red (POST fc/gt2/*)
    ANTES de que el iframe de Arkose cargue completamente. Una vez que
    el iframe renderiza, el blob se invalida.
"""
import os
import json
import time
import logging
import httpx
import asyncio
import re
import urllib.parse

logger = logging.getLogger("CaptchaSolver")

# ───────── API Keys admitidas ─────────
NOCAPTCHAAI_API_KEY = os.getenv("NOCAPTCHAAI_API_KEY", "")
CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "")
CAPMONSTER_API_KEY = os.getenv("CAPMONSTER_API_KEY", "")

# Configurar la API Key activa por defecto
API_KEY = CAPMONSTER_API_KEY or NOCAPTCHAAI_API_KEY or CAPSOLVER_API_KEY
API_URL = "https://api.capmonster.cloud" if CAPMONSTER_API_KEY else ("https://api.nocaptchaai.com" if NOCAPTCHAAI_API_KEY else "https://api.capsolver.com")

if CAPMONSTER_API_KEY:
    logger.info("Utilizando motor de resolución CapMonster por defecto")
elif NOCAPTCHAAI_API_KEY:
    logger.info("Utilizando motor de resolución noCaptchaAi (Plan Gratis/Billetera) por defecto")
elif CAPSOLVER_API_KEY:
    logger.info("Utilizando motor de resolución CapSolver (Fallback) por defecto")
else:
    logger.warning("Ninguna API Key de CAPTCHA ha sido configurada en el entorno (.env).")

# ───────── User-Agent oficial de CapMonster (docs.capmonster.cloud) ─────────
# NUNCA cambiar esto sin verificar en https://capmonster.cloud/api/useragent/actual
CAPMONSTER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"


# ═══════════════════════════════════════════════════════════════════════
#  NUEVO SISTEMA: Interceptor de Blob en tráfico de red
# ═══════════════════════════════════════════════════════════════════════

async def setup_blob_interceptor(page) -> dict:
    """
    Configura un interceptor de requests usando page.route() para atrapar
    el blob del tráfico de red cuando Arkose Labs hace el POST a fc/gt2/*.
    
    Usa page.route() en vez de page.on("request") porque route() da acceso
    garantizado al body del POST y es más confiable para requests de iframes.
    
    DEBE llamarse ANTES de que el CAPTCHA aparezca (antes del login).
    """
    captured = {"blob": "", "public_key": "", "surl": ""}
    
    # ── Interceptor via route() para requests a Arkose Labs ──
    async def intercept_arkose(route):
        request = route.request
        url = request.url
        url_lower = url.lower()
        
        logger.info(f"🔎 [INTERCEPTOR] {request.method} → {url[:120]}")
        
        # Capturar datos del POST a fc/gt2 (contiene el blob)
        if request.method == "POST":
            try:
                post_data = request.post_data
                if post_data:
                    logger.info(f"📦 [INTERCEPTOR] POST body ({len(post_data)} bytes)")
                    # Log primeros 500 chars del body para debug
                    logger.info(f"📦 [INTERCEPTOR] Body RAW (primeros 500): {post_data[:500]}")
                    
                    # Intentar parsear como form-encoded
                    parsed = urllib.parse.parse_qs(post_data)
                    logger.info(f"📦 [INTERCEPTOR] Claves encontradas: {list(parsed.keys())[:20]}")
                    
                    # ── Estrategia 1: data[blob] (formato estándar Arkose) ──
                    if 'data[blob]' in parsed and parsed['data[blob]'][0]:
                        captured["blob"] = parsed['data[blob]'][0]
                        logger.info(f"🎯🎯🎯 ¡BLOB INTERCEPTADO (data[blob])! {captured['blob'][:60]}...")
                    
                    # ── Estrategia 2: blob directo ──
                    elif 'blob' in parsed and parsed['blob'][0]:
                        captured["blob"] = parsed['blob'][0]
                        logger.info(f"🎯🎯🎯 ¡BLOB INTERCEPTADO (blob)! {captured['blob'][:60]}...")
                    
                    # ── Estrategia 3: data directo ──
                    elif 'data' in parsed and parsed['data'][0]:
                        captured["blob"] = parsed['data'][0]
                        logger.info(f"🎯🎯🎯 ¡BLOB INTERCEPTADO (data)! {captured['blob'][:60]}...")
                    
                    # ── Estrategia 4: buscar 'blob' en cualquier clave ──
                    else:
                        for key, values in parsed.items():
                            if 'blob' in key.lower() and values and values[0]:
                                captured["blob"] = values[0]
                                logger.info(f"🎯🎯🎯 ¡BLOB INTERCEPTADO ({key})! {captured['blob'][:60]}...")
                                break
                    
                    # ── Estrategia 5: si es JSON en vez de form-encoded ──
                    if not captured["blob"]:
                        try:
                            import json as json_mod
                            json_data = json_mod.loads(post_data)
                            if isinstance(json_data, dict):
                                logger.info(f"📦 [INTERCEPTOR] Body es JSON. Claves: {list(json_data.keys())[:20]}")
                                blob_val = json_data.get("blob") or json_data.get("data", {}).get("blob") if isinstance(json_data.get("data"), dict) else None
                                if blob_val:
                                    captured["blob"] = blob_val
                                    logger.info(f"🎯🎯🎯 ¡BLOB INTERCEPTADO (JSON)! {captured['blob'][:60]}...")
                        except (json.JSONDecodeError, ValueError):
                            pass
                    
                    # Extraer public key del POST
                    if 'public_key' in parsed and parsed['public_key'][0]:
                        captured["public_key"] = parsed['public_key'][0]
                        logger.info(f"🔑 PUBLIC KEY del POST: {captured['public_key']}")
                    
                    # Extraer surl
                    if 'surl' in parsed and parsed['surl'][0]:
                        captured["surl"] = parsed['surl'][0]
                        logger.info(f"🌐 SURL del POST: {captured['surl']}")
                else:
                    logger.debug(f"[INTERCEPTOR] POST sin body: {url[:80]}")
            except Exception as e:
                logger.warning(f"[INTERCEPTOR] Error parseando POST: {e}")
        
        # Extraer public key de la URL (para GET requests como iframes)
        try:
            parsed_url = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed_url.query)
            if 'pk' in params and not captured["public_key"]:
                captured["public_key"] = params['pk'][0]
                logger.info(f"🔑 PUBLIC KEY de URL: {captured['public_key']}")
            if 'public_key' in params and not captured["public_key"]:
                captured["public_key"] = params['public_key'][0]
            if parsed_url.netloc and not captured["surl"]:
                captured["surl"] = f"https://{parsed_url.netloc}"
        except:
            pass
        
        # CONTINUAR la request normalmente (no la bloqueamos)
        await route.continue_()
    
    # Registrar interceptor para TODAS las URLs de Arkose Labs
    await page.route("**/*arkoselabs*/**", intercept_arkose)
    await page.route("**/*funcaptcha*/**", intercept_arkose)
    await page.route("**/fc/gt2/**", intercept_arkose)
    # También capturar el endpoint específico de Microsoft
    await page.route("**/*client-api*/**", intercept_arkose)
    
    logger.info("🛡️ Interceptor de blob configurado (via page.route). Esperando tráfico de Arkose Labs...")
    return captured


async def wait_for_blob(captured: dict, timeout_seconds: int = 30) -> bool:
    """
    Espera hasta que el blob sea interceptado o se agote el timeout.
    """
    for i in range(timeout_seconds * 2):  # Checkear cada 0.5s
        if captured.get("blob"):
            return True
        await asyncio.sleep(0.5)
    return False


async def click_arkose_start_button(page) -> bool:
    """
    Hace clic en el botón 'Start' de Arkose Labs para iniciar el CAPTCHA.
    Este clic es CRÍTICO porque dispara el POST a fc/gt2 que contiene el blob.
    El interceptor (setup_blob_interceptor) debe estar activo ANTES de este clic.
    """
    logger.info("🔍 Buscando botón 'Start' del CAPTCHA en la página e iframes...")
    
    # Esperar a que los iframes de Arkose terminen de cargar
    await page.wait_for_timeout(3000)
    
    button_texts = ["Start", "Comenzar", "Verify", "Verificar", "Authenticate"]
    
    # Buscar en la página principal Y en todos los iframes
    frames_to_check = [page] + page.frames
    
    for frame in frames_to_check:
        for text in button_texts:
            try:
                btn = frame.locator(
                    f"button:has-text('{text}'), "
                    f"a:has-text('{text}'), "
                    f"div[role='button']:has-text('{text}'), "
                    f".fc-button"
                ).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    logger.info(f"🔘 ¡Botón '{text}' clickeado! Esperando que se genere el blob...")
                    # Dar tiempo para que Arkose haga el POST a fc/gt2 con el blob
                    await page.wait_for_timeout(5000)
                    return True
            except:
                pass
    
    logger.warning("⚠️ No se encontró botón 'Start' (puede que ya se haya clickeado o no exista).")
    return False


# ═══════════════════════════════════════════════════════════════════════
#  FUNCIÓN PRINCIPAL: Resolver CAPTCHA en página
# ═══════════════════════════════════════════════════════════════════════

async def solve_captcha_on_page(page, network_blob="", intercepted_data=None) -> bool:
    """
    Intenta resolver cualquier CAPTCHA presente en la página usando la API configurada.
    
    Args:
        page: Página de Playwright
        network_blob: Blob pre-capturado del tráfico de red (legacy)
        intercepted_data: Dict con datos interceptados por setup_blob_interceptor()
    
    Retorna True si se resolvió exitosamente, False si no.
    """
    if not API_KEY:
        logger.error("No se ha configurado ninguna API Key de CAPTCHA.")
        return False

    current_url = page.url
    logger.info(f"Detectando tipo de CAPTCHA en: {current_url}")

    # Tomar screenshot para diagnóstico
    try:
        debug_path = "/app/persist/debug_captcha_page.png" if os.path.exists("/app/persist") else "debug_captcha_page.png"
        await page.screenshot(path=debug_path)
    except:
        pass

    # Obtener el HTML de la página para detectar tipo de CAPTCHA
    try:
        page_content = await page.content()
    except:
        page_content = ""

    # ===== Detectar tipo de CAPTCHA =====
    iframe_urls = " ".join([f.url for f in page.frames])
    iframe_names = " ".join([f.name or "" for f in page.frames])
    combined_content = (page_content + " " + iframe_urls + " " + iframe_names).lower()

    # 1. Arkose Labs / FunCaptcha (El que usa Microsoft login)
    is_funcaptcha = (
        "arkoselabs" in combined_content or
        "funcaptcha" in combined_content or
        "game-core-frame" in combined_content or
        "enforcement-frame" in combined_content or
        "fc-iframe" in combined_content or
        "/captchaa" in page.url.lower() or
        "/captcha" in page.url.lower() or
        "use the arrows" in combined_content or
        "pick the image" in combined_content or
        "security verification" in combined_content
    )
    
    if is_funcaptcha:
        logger.info("⚡ CAPTCHA Detectado: Arkose Labs / FunCaptcha")
        logger.info(f"   Señales: iframes={iframe_names[:100]}, url={page.url}")
        return await solve_funcaptcha(page, page_content, network_blob, intercepted_data)

    # 2. reCAPTCHA
    if "recaptcha" in page_content.lower() or "grecaptcha" in page_content:
        logger.info("⚡ CAPTCHA Detectado: reCAPTCHA")
        return await solve_recaptcha(page, page_content)

    # 3. hCaptcha
    if "hcaptcha" in page_content.lower():
        logger.info("⚡ CAPTCHA Detectado: hCaptcha")
        return await solve_hcaptcha(page, page_content)

    # 4. Microsoft HIP / Custom CAPTCHA - intentar con screenshot
    logger.info("CAPTCHA no reconocido en firmas tradicionales. Intentando resolver por screenshot...")
    return await solve_by_screenshot(page)


# ═══════════════════════════════════════════════════════════════════════
#  RESOLVER: Arkose Labs / FunCaptcha
# ═══════════════════════════════════════════════════════════════════════

async def solve_funcaptcha(page, page_content: str, network_blob: str = "", intercepted_data: dict = None) -> bool:
    """
    Resuelve Arkose Labs / FunCaptcha usando la API de CapMonster.
    
    Flujo correcto según docs.capmonster.cloud:
    1. El blob se obtiene del interceptor de red (setup_blob_interceptor)
    2. El User-Agent DEBE ser Chrome/147 exactamente
    3. Se envía como FunCaptchaTaskProxyless
    """
    
    # ───── 1. Obtener blob (la pieza más crítica) ─────
    # El blob se genera cuando el usuario hace clic en "Start" del CAPTCHA.
    # El interceptor (setup_blob_interceptor) debe estar activo para atrapar
    # el POST a fc/gt2 que contiene el blob.
    data_blob = ""
    
    # Prioridad 1: Si ya lo tenemos del interceptor (el usuario ya clickeó Start)
    if intercepted_data and intercepted_data.get("blob"):
        data_blob = intercepted_data["blob"]
        logger.info(f"✅ Blob ya disponible del interceptor: {data_blob[:50]}...")
    
    # Prioridad 2: Blob pasado como parámetro (legacy)
    if not data_blob and network_blob:
        data_blob = network_blob
        logger.info(f"✅ Blob obtenido del parámetro legacy: {data_blob[:50]}...")
    
    # Prioridad 3: Hacer clic en "Start" para GENERAR el blob y atraparlo
    if not data_blob:
        logger.info("🔘 Haciendo clic en 'Start' para generar el blob...")
        await click_arkose_start_button(page)
        
        # Esperar a que el interceptor atrape el blob del POST fc/gt2
        if intercepted_data is not None:
            logger.info("⏳ Esperando que el interceptor capture el blob (máx 20s)...")
            found = await wait_for_blob(intercepted_data, timeout_seconds=20)
            if found:
                data_blob = intercepted_data["blob"]
                logger.info(f"🎯 ¡BLOB CAPTURADO tras clic en Start! {data_blob[:50]}...")
            else:
                logger.warning("⚠️ Blob no apareció tras 20s. Intentando extraer de iframes...")
    
    # Prioridad 4: Intentar extraer de los iframes (menos confiable)
    if not data_blob:
        for frame in page.frames:
            try:
                frame_url = frame.url
                if "arkoselabs" in frame_url or "funcaptcha" in frame_url.lower():
                    parsed_url = urllib.parse.urlparse(frame_url)
                    params = urllib.parse.parse_qs(parsed_url.query)
                    if 'sdata' in params and params['sdata'][0]:
                        data_blob = params['sdata'][0]
                        logger.info(f"📎 Blob extraído de iframe (sdata): {data_blob[:50]}...")
                        break
                    elif 'data' in params and params['data'][0]:
                        data_blob = params['data'][0]
                        logger.info(f"📎 Blob extraído de iframe (data): {data_blob[:50]}...")
                        break
            except:
                continue
    
    if not data_blob:
        logger.warning("⚠️ BLOB NO DISPONIBLE. Enviando tarea SIN blob (puede fallar en Microsoft).")

    # ───── 2. Obtener public key ─────
    public_key = ""
    
    # Del interceptor
    if intercepted_data and intercepted_data.get("public_key"):
        public_key = intercepted_data["public_key"]
    
    # De los iframes
    if not public_key:
        for frame in page.frames:
            try:
                frame_url = frame.url
                if "arkoselabs" in frame_url or "funcaptcha" in frame_url.lower():
                    pk_match = re.search(r'pk=([a-fA-F0-9-]+)', frame_url)
                    if pk_match:
                        public_key = pk_match.group(1)
                        break
                    parsed_url = urllib.parse.urlparse(frame_url)
                    params = urllib.parse.parse_qs(parsed_url.query)
                    if 'public_key' in params:
                        public_key = params['public_key'][0]
                        break
            except:
                continue
    
    # Del HTML
    if not public_key:
        pk_match = re.search(r'publicKey["\s:]+["\'"]([a-fA-F0-9-]+)["\']', page_content)
        if pk_match:
            public_key = pk_match.group(1)
    
    # Fallback Microsoft
    if not public_key:
        public_key = "B7D8911C-5CCF-4C2E-9F40-9E14136697FA"
        logger.warning(f"No se extrajo public key. Usando fallback de Microsoft: {public_key}")

    # ───── 3. Obtener subdomain ─────
    # IMPORTANTE: Microsoft usa Azure WAF para proxied Arkose, así que la URL real
    # del iframe es visualsupport.microsoft.com/.azwaf/captcha/proxied/...
    # Pero para CapMonster, funcaptchaApiJSSubdomain debe ser el dominio del API
    # de Arkose Labs (no el proxy de Microsoft).
    # Para Azure WAF de Microsoft, se usa el subdominio proxied de Microsoft.
    subdomain = ""
    
    # Intentar extraer el surl del POST body (es la URL del API de Arkose)
    if intercepted_data and intercepted_data.get("surl"):
        surl = intercepted_data["surl"]
        subdomain = surl.replace("https://", "").replace("http://", "")
        logger.info(f"   SURL original del interceptor: {subdomain}")
    
    # Si el surl apunta al proxy de Microsoft, usar el formato correcto
    # Microsoft proxied Arkose: visualsupport.microsoft.com/.azwaf/captcha/proxied/
    if subdomain and "microsoft.com" in subdomain:
        # Para Azure WAF, el subdomain debe apuntar al proxy de Microsoft
        subdomain = subdomain.split("/")[0]  # Solo el dominio base
        logger.info(f"   Subdomain ajustado (Azure WAF proxy): {subdomain}")
    
    # Fallback: usar el dominio estándar de Arkose Labs
    if not subdomain:
        subdomain = "client-api.arkoselabs.com"

    website_url = page.url
    logger.info(f"📋 FunCaptcha Parámetros Finales:")
    logger.info(f"   PK (sitekey): {public_key}")
    logger.info(f"   URL: {website_url}")
    logger.info(f"   Subdomain: {subdomain}")
    logger.info(f"   Blob: {'✅ SÍ (' + data_blob[:40] + '...)' if data_blob else '❌ NO'}")
    logger.info(f"   UserAgent: {CAPMONSTER_USER_AGENT}")

    # ───── 4. Seleccionar API ─────
    if CAPMONSTER_API_KEY:
        target_api_url = "https://api.capmonster.cloud"
        target_api_key = CAPMONSTER_API_KEY
    elif CAPSOLVER_API_KEY:
        target_api_url = "https://api.capsolver.com"
        target_api_key = CAPSOLVER_API_KEY
    else:
        target_api_url = API_URL
        target_api_key = API_KEY

    # ───── 5. Enviar solicitud a CapMonster con reintentos automáticos ─────
    # Azure WAF CAPTCHAs requieren estrategias diferentes a FunCaptcha estándar.
    # Probamos múltiples combinaciones de tipo de tarea, subdomain, y blob.
    strategies = [
        # (task_type, subdomain, include_blob, label)
        ("FunCaptchaTaskProxyless", "client-api.arkoselabs.com", True, "Proxyless + Arkose subdomain + blob"),
        ("FunCaptchaTaskProxyless", "client-api.arkoselabs.com", False, "Proxyless + Arkose subdomain SIN blob"),
        ("FunCaptchaTaskProxyless", subdomain, True, f"Proxyless + {subdomain} + blob"),
        ("FunCaptchaTaskProxyless", "", False, "Proxyless + sin subdomain + sin blob"),
    ]
    
    # Eliminar duplicados
    seen = set()
    unique_strategies = []
    for s in strategies:
        key = (s[0], s[1], s[2])
        if key not in seen:
            seen.add(key)
            unique_strategies.append(s)
    
    async with httpx.AsyncClient(timeout=180) as client:
        for attempt, (task_type, current_subdomain, use_blob, label) in enumerate(unique_strategies):
            logger.info(f"\n{'='*60}")
            logger.info(f"🔄 INTENTO {attempt+1}/{len(unique_strategies)} — {label}")
            logger.info(f"{'='*60}")
            
            # Construir payload
            task_data = {
                "type": task_type,
                "websiteURL": website_url,
                "websitePublicKey": public_key,
                "userAgent": CAPMONSTER_USER_AGENT
            }
            
            # Solo agregar subdomain si no está vacío
            if current_subdomain:
                task_data["funcaptchaApiJSSubdomain"] = current_subdomain
            
            payload = {
                "clientKey": target_api_key,
                "task": task_data
            }
            
            # Agregar blob si corresponde
            if use_blob and data_blob:
                payload["task"]["data"] = json.dumps({"blob": data_blob})
            
            logger.info(f"📤 REQUEST BODY:\n{json.dumps(payload, indent=2)}")
                
            create_resp = await client.post(f"{target_api_url}/createTask", json=payload)
            
            if create_resp.status_code != 200:
                logger.error(f"Error HTTP creando tarea: {create_resp.status_code}")
                continue

            create_data = create_resp.json()

            if create_data.get("errorId", 1) != 0:
                error_code = create_data.get("errorCode", "UNKNOWN")
                error_desc = create_data.get("errorDescription", "")
                logger.error(f"Error API: [{error_code}] {error_desc}")
                if "USERAGENT" in error_code.upper():
                    logger.error("💡 Verificar User-Agent en https://capmonster.cloud/api/useragent/actual")
                continue

            task_id = create_data["taskId"]
            logger.info(f"✅ Tarea creada. ID: {task_id}. Esperando solución...")

            # Esperar resultado (máximo 75 segundos por intento)
            solved = False
            for i in range(25):  # 25 * 3s = 75s
                await asyncio.sleep(3)
                
                try:
                    result_resp = await client.post(f"{target_api_url}/getTaskResult", json={
                        "clientKey": target_api_key,
                        "taskId": task_id
                    })
                except Exception as e:
                    logger.warning(f"Error HTTP en getTaskResult: {e}")
                    continue
                
                if result_resp.status_code != 200:
                    continue

                result_data = result_resp.json()
                status = result_data.get("status", "")

                if status == "ready":
                    token = result_data["solution"]["token"]
                    logger.info("🎉🎉🎉 ¡FunCaptcha RESUELTO por CapMonster!")
                    logger.info(f"   Token: {token[:80]}...")

                    try:
                        await inject_funcaptcha_token(page, token)
                    except Exception as e:
                        logger.warning(f"Error inyectando token: {e}")
                    
                    await page.wait_for_timeout(3000)
                    return True

                if status == "failed" or result_data.get("errorId", 0) != 0:
                    error_code = result_data.get("errorCode", "UNKNOWN")
                    logger.warning(f"❌ Intento {attempt+1} falló: [{error_code}]")
                    if "UNSOLVABLE" in error_code:
                        logger.info("   Reintentando con diferente configuración...")
                    break  # Salir del polling loop, probar siguiente estrategia
                
                if (i + 1) % 10 == 0:
                    logger.info(f"   ⏳ Esperando... ({(i+1)*3}s)")
            
            if solved:
                return True

    logger.error("❌ Timeout: CapMonster no resolvió en 180 segundos.")
    return False


async def inject_funcaptcha_token(page, token: str):
    """Inyecta el token resuelto de FunCaptcha en la página."""
    # Método 1: Inyectar via ArkoseEnforcement (método oficial)
    await page.evaluate(f"""
        (function() {{
            // Intentar via ArkoseEnforcement
            if (window.ArkoseEnforcement) {{
                window.ArkoseEnforcement.setConfig({{data: {{token: "{token}"}}}});
            }}
            
            // Intentar via el objeto FC global
            if (window.FC_CALLBACK) {{
                window.FC_CALLBACK("{token}");
            }}
            
            // Intentar disparar callbacks genéricos
            if (typeof captchaCallback === 'function') captchaCallback("{token}");
            if (typeof verifyCallback === 'function') verifyCallback("{token}");
            
            // Buscar y llenar inputs ocultos de verificación
            var inputs = document.querySelectorAll('input[name*="fc-token"], input[name*="verification"]');
            inputs.forEach(function(inp) {{ inp.value = "{token}"; }});
            
            // Intentar via postMessage a iframes de Arkose
            var frames = document.querySelectorAll('iframe');
            frames.forEach(function(f) {{
                try {{
                    f.contentWindow.postMessage(JSON.stringify({{
                        eventId: "challenge-complete",
                        payload: {{sessionToken: "{token}"}}
                    }}), "*");
                }} catch(e) {{}}
            }});
        }})();
    """)
    logger.info("💉 Token inyectado en la página via múltiples métodos.")


# ═══════════════════════════════════════════════════════════════════════
#  RESOLVER: reCAPTCHA
# ═══════════════════════════════════════════════════════════════════════

async def solve_recaptcha(page, page_content: str) -> bool:
    """Resuelve reCAPTCHA v2 usando la API."""
    site_key_match = re.search(r'sitekey["\s:=]+["\'"]([a-zA-Z0-9_-]+)["\']', page_content, re.I)
    if not site_key_match:
        logger.error("No se pudo extraer sitekey de reCAPTCHA")
        return False

    site_key = site_key_match.group(1)
    website_url = page.url

    async with httpx.AsyncClient(timeout=120) as client:
        create_resp = await client.post(f"{API_URL}/createTask", json={
            "clientKey": API_KEY,
            "task": {
                "type": "ReCaptchaV2TaskProxyLess",
                "websiteURL": website_url,
                "websiteKey": site_key,
            }
        })
        create_data = create_resp.json()
        if create_data.get("errorId", 1) != 0:
            logger.error(f"Error creando tarea reCAPTCHA: {create_data}")
            return False

        task_id = create_data["taskId"]
        for _ in range(60):
            await asyncio.sleep(2)
            result_resp = await client.post(f"{API_URL}/getTaskResult", json={
                "clientKey": API_KEY,
                "taskId": task_id
            })
            result_data = result_resp.json()
            if result_data.get("status") == "ready":
                token = result_data["solution"]["gRecaptchaResponse"]
                await page.evaluate(f'document.getElementById("g-recaptcha-response").innerHTML = "{token}"')
                await page.evaluate(f'if(typeof ___grecaptcha_cfg !== "undefined") Object.values(___grecaptcha_cfg.clients)[0].callback("{token}")')
                await page.wait_for_timeout(3000)
                return True
            if result_data.get("status") == "failed":
                return False

    return False


# ═══════════════════════════════════════════════════════════════════════
#  RESOLVER: hCaptcha
# ═══════════════════════════════════════════════════════════════════════

async def solve_hcaptcha(page, page_content: str) -> bool:
    """Resuelve hCaptcha usando la API."""
    site_key_match = re.search(r'sitekey["\s:=]+["\'"]([a-f0-9-]+)["\']', page_content, re.I)
    if not site_key_match:
        return False

    site_key = site_key_match.group(1)
    website_url = page.url

    async with httpx.AsyncClient(timeout=120) as client:
        create_resp = await client.post(f"{API_URL}/createTask", json={
            "clientKey": API_KEY,
            "task": {
                "type": "HCaptchaTaskProxyLess",
                "websiteURL": website_url,
                "websiteKey": site_key,
            }
        })
        create_data = create_resp.json()
        if create_data.get("errorId", 1) != 0:
            return False

        task_id = create_data["taskId"]
        for _ in range(60):
            await asyncio.sleep(2)
            result_resp = await client.post(f"{API_URL}/getTaskResult", json={
                "clientKey": API_KEY,
                "taskId": task_id
            })
            result_data = result_resp.json()
            if result_data.get("status") == "ready":
                token = result_data["solution"]["gRecaptchaResponse"]
                await page.evaluate(f"""
                    document.querySelector('[name="h-captcha-response"]').value = "{token}";
                    document.querySelector('[name="g-recaptcha-response"]').value = "{token}";
                """)
                await page.wait_for_timeout(3000)
                return True
            if result_data.get("status") == "failed":
                return False
    return False


# ═══════════════════════════════════════════════════════════════════════
#  RESOLVER: Screenshot (último recurso)
# ═══════════════════════════════════════════════════════════════════════

async def solve_by_screenshot(page) -> bool:
    """
    Último recurso: toma screenshot del CAPTCHA y lo envía para ImageToText.
    """
    import base64

    try:
        screenshot_bytes = await page.screenshot()
        image_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')

        async with httpx.AsyncClient(timeout=120) as client:
            create_resp = await client.post(f"{API_URL}/createTask", json={
                "clientKey": API_KEY,
                "task": {
                    "type": "ImageToTextTask",
                    "body": image_b64,
                    "images": [image_b64],
                }
            })
            create_data = create_resp.json()
            if create_data.get("errorId", 1) != 0:
                logger.error(f"Error en Screenshot ImageToText: {create_data}")
                return False

            task_id = create_data["taskId"]
            for _ in range(30):
                await asyncio.sleep(2)
                result_resp = await client.post(f"{API_URL}/getTaskResult", json={
                    "clientKey": API_KEY,
                    "taskId": task_id
                })
                result_data = result_resp.json()
                if result_data.get("status") == "ready":
                    text = result_data["solution"]["text"]
                    logger.info(f"CAPTCHA visual resuelto: {text}")

                    try:
                        captcha_input = page.locator("input[type='text']").first
                        if await captcha_input.is_visible(timeout=2000):
                            await captcha_input.fill(text)
                            submit_btn = page.locator("button[type='submit'], input[type='submit']").first
                            if await submit_btn.is_visible(timeout=2000):
                                await submit_btn.click()
                            await page.wait_for_timeout(3000)
                            return True
                    except:
                        pass
                    return False
                if result_data.get("status") == "failed":
                    return False
    except Exception as e:
        logger.error(f"Error en solve_by_screenshot: {e}")

    return False
