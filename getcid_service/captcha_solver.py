"""
Módulo de resolución automática de CAPTCHA para GetCID.
Usa la API de noCaptchaAi (200 resoluciones GRATIS por día de por vida).
"""
import os
import json
import time
import logging
import httpx
import asyncio

logger = logging.getLogger("CaptchaSolver")

# API Keys admitidas (noCaptchaAi es la principal, CapSolver como fallback)
NOCAPTCHAAI_API_KEY = os.getenv("NOCAPTCHAAI_API_KEY", "")
CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "")

# Usar noCaptchaAi por defecto si está configurado, de lo contrario CapSolver
if NOCAPTCHAAI_API_KEY:
    API_KEY = NOCAPTCHAAI_API_KEY
    API_URL = "https://api.nocaptchaai.com"
    logger.info("Utilizando motor de resolución noCaptchaAi (Plan Gratis/Billetera)")
elif CAPSOLVER_API_KEY:
    API_KEY = CAPSOLVER_API_KEY
    API_URL = "https://api.capsolver.com"
    logger.info("Utilizando motor de resolución CapSolver (Fallback)")
else:
    API_KEY = ""
    API_URL = "https://api.nocaptchaai.com"
    logger.warning("Ninguna API Key de CAPTCHA ha sido configurada en el entorno (.env).")


async def solve_captcha_on_page(page) -> bool:
    """
    Intenta resolver cualquier CAPTCHA presente en la página usando la API configurada.
    Retorna True si se resolvió exitosamente, False si no.
    """
    if not API_KEY:
        logger.error("No se ha configurado ninguna API Key de CAPTCHA (NOCAPTCHAAI_API_KEY).")
        return False

    current_url = page.url
    logger.info(f"Detectando tipo de CAPTCHA en: {current_url}")

    # Tomar screenshot para diagnóstico
    try:
        await page.screenshot(path="/app/persist/debug_captcha_page.png" if os.path.exists("/app/persist") else "debug_captcha_page.png")
    except:
        pass

    # Obtener el HTML de la página para detectar tipo de CAPTCHA
    try:
        page_content = await page.content()
    except:
        page_content = ""

    # ===== Detectar tipo de CAPTCHA =====

    # 1. Arkose Labs / FunCaptcha (El que usa Microsoft login)
    if "arkoselabs" in page_content or "funcaptcha" in page_content.lower():
        logger.info("⚡ CAPTCHA Detectado: Arkose Labs / FunCaptcha")
        return await solve_funcaptcha(page, page_content)

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


async def solve_funcaptcha(page, page_content: str) -> bool:
    """Resuelve Arkose Labs / FunCaptcha usando la API."""
    import re

    # Extraer public key del FunCaptcha (sitekey)
    # Microsoft usa típicamente la key: B7D8911C-5CCF-4C2E-9F40-9E14136697FA u otras similares
    pk_match = re.search(r'publicKey["\s:]+["\']([a-fA-F0-9-]+)["\']', page_content)
    if not pk_match:
        pk_match = re.search(r'pk["\s:]+["\']([a-fA-F0-9-]+)["\']', page_content)
    
    # Buscar en iframes de la página si no está en el core HTML
    if not pk_match:
        frames = page.frames
        for frame in frames:
            try:
                frame_url = frame.url
                if "arkoselabs" in frame_url or "funcaptcha" in frame_url.lower():
                    pk_match = re.search(r'pk=([a-fA-F0-9-]+)', frame_url)
                    if pk_match:
                        break
            except:
                continue

    if pk_match:
        public_key = pk_match.group(1)
    else:
        # Fallback para Microsoft Login: si no se encuentra la key, usar la key fija conocida de MS
        public_key = "B7D8911C-5CCF-4C2E-9F40-9E14136697FA"
        logger.warning(f"No se pudo extraer dinámicamente. Usando sitekey fija de Microsoft: {public_key}")

    website_url = page.url
    logger.info(f"FunCaptcha PK (sitekey): {public_key} | URL: {website_url}")

    # Enviar solicitud a la API
    async with httpx.AsyncClient(timeout=120) as client:
        # Crear tarea
        create_resp = await client.post(f"{API_URL}/createTask", json={
            "clientKey": API_KEY,
            "task": {
                "type": "FunCaptchaTaskProxyLess",
                "websiteURL": website_url,
                "websitePublicKey": public_key,
            }
        })
        
        if create_resp.status_code != 200:
            logger.error(f"Error HTTP creando tarea en {API_URL}: Código {create_resp.status_code} | Respuesta: {create_resp.text}")
            return False

        create_data = create_resp.json()

        if create_data.get("errorId", 1) != 0:
            logger.error(f"Error de la API de resolución al crear tarea: {create_data}")
            return False

        task_id = create_data["taskId"]
        logger.info(f"Tarea creada exitosamente. ID: {task_id}. Esperando solución...")

        # Esperar resultado (máximo 120 segundos)
        for i in range(60):
            await asyncio.sleep(2)
            result_resp = await client.post(f"{API_URL}/getTaskResult", json={
                "clientKey": API_KEY,
                "taskId": task_id
            })
            
            if result_resp.status_code != 200:
                continue

            result_data = result_resp.json()

            if result_data.get("status") == "ready":
                token = result_data["solution"]["token"]
                logger.info("✅ ¡FunCaptcha resuelto por la API exitosamente!")

                # Inyectar token en la página para saltar el CAPTCHA
                await page.evaluate(f"""
                    if (window.ArkoseEnforcement) {{
                        window.ArkoseEnforcement.setConfig({{data: {{token: "{token}"}}}});
                    }}
                    // Intentar disparar callbacks genéricos de envío
                    if (typeof captchaCallback === 'function') captchaCallback("{token}");
                    if (typeof verifyCallback === 'function') verifyCallback("{token}");
                """)
                await page.wait_for_timeout(3000)
                return True

            if result_data.get("status") == "failed":
                logger.error(f"La API reportó fallo al resolver: {result_data}")
                return False

    logger.error("Timeout de la API de resolución (120s alcanzado)")
    return False


async def solve_recaptcha(page, page_content: str) -> bool:
    """Resuelve reCAPTCHA v2 usando la API."""
    import re

    site_key_match = re.search(r'sitekey["\s:=]+["\']([a-zA-Z0-9_-]+)["\']', page_content, re.I)
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


async def solve_hcaptcha(page, page_content: str) -> bool:
    """Resuelve hCaptcha usando la API."""
    import re

    site_key_match = re.search(r'sitekey["\s:=]+["\']([a-f0-9-]+)["\']', page_content, re.I)
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
