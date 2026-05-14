"""
Módulo de resolución automática de CAPTCHA para GetCID.
Usa CapSolver API para resolver CAPTCHAs de Microsoft automáticamente.
Costo: ~$0.002 por CAPTCHA resuelto (~$1.50/mes).
"""
import os
import json
import time
import logging
import httpx

logger = logging.getLogger("CaptchaSolver")

CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "")
CAPSOLVER_API_URL = "https://api.capsolver.com"


async def solve_captcha_on_page(page) -> bool:
    """
    Intenta resolver cualquier CAPTCHA presente en la página.
    Retorna True si se resolvió exitosamente, False si no.
    """
    if not CAPSOLVER_API_KEY:
        logger.error("CAPSOLVER_API_KEY no configurada. No se puede resolver CAPTCHA automáticamente.")
        return False

    current_url = page.url
    logger.info(f"Intentando resolver CAPTCHA en: {current_url}")

    # Tomar screenshot para diagnóstico
    try:
        await page.screenshot(path="/app/debug_captcha_page.png")
    except:
        pass

    # Obtener el HTML de la página para detectar tipo de CAPTCHA
    try:
        page_content = await page.content()
    except:
        page_content = ""

    # ===== Detectar tipo de CAPTCHA =====

    # 1. Arkose Labs / FunCaptcha
    if "arkoselabs" in page_content or "funcaptcha" in page_content.lower():
        logger.info("Detectado: Arkose Labs / FunCaptcha")
        return await solve_funcaptcha(page, page_content)

    # 2. reCAPTCHA
    if "recaptcha" in page_content.lower() or "grecaptcha" in page_content:
        logger.info("Detectado: reCAPTCHA")
        return await solve_recaptcha(page, page_content)

    # 3. hCaptcha
    if "hcaptcha" in page_content.lower():
        logger.info("Detectado: hCaptcha")
        return await solve_hcaptcha(page, page_content)

    # 4. Microsoft HIP / Custom CAPTCHA - intentar con screenshot
    logger.info("CAPTCHA no reconocido. Intentando resolver por screenshot...")
    return await solve_by_screenshot(page)


async def solve_funcaptcha(page, page_content: str) -> bool:
    """Resuelve Arkose Labs / FunCaptcha usando CapSolver."""
    import re

    # Extraer public key del FunCaptcha
    pk_match = re.search(r'publicKey["\s:]+["\']([a-fA-F0-9-]+)["\']', page_content)
    if not pk_match:
        pk_match = re.search(r'pk["\s:]+["\']([a-fA-F0-9-]+)["\']', page_content)
    if not pk_match:
        # Buscar en iframes
        frames = page.frames
        for frame in frames:
            try:
                frame_url = frame.url
                if "arkoselabs" in frame_url:
                    pk_match = re.search(r'pk=([a-fA-F0-9-]+)', frame_url)
                    if pk_match:
                        break
            except:
                continue

    if not pk_match:
        logger.error("No se pudo extraer public key de FunCaptcha")
        return False

    public_key = pk_match.group(1)
    website_url = page.url
    logger.info(f"FunCaptcha PK: {public_key}")

    # Enviar a CapSolver
    async with httpx.AsyncClient(timeout=120) as client:
        # Crear tarea
        create_resp = await client.post(f"{CAPSOLVER_API_URL}/createTask", json={
            "appId": "6A47C22E-E45E-4023-A8D3-E498E2B475E6",
            "clientKey": CAPSOLVER_API_KEY,
            "task": {
                "type": "FunCaptchaTaskProxyLess",
                "websiteURL": website_url,
                "websitePublicKey": public_key,
            }
        })
        create_data = create_resp.json()

        if create_data.get("errorId", 1) != 0:
            logger.error(f"CapSolver error creando tarea: {create_data}")
            return False

        task_id = create_data["taskId"]
        logger.info(f"Tarea CapSolver creada: {task_id}")

        # Esperar resultado (máximo 120 segundos)
        for _ in range(60):
            await asyncio.sleep(2)
            result_resp = await client.post(f"{CAPSOLVER_API_URL}/getTaskResult", json={
                "clientKey": CAPSOLVER_API_KEY,
                "taskId": task_id
            })
            result_data = result_resp.json()

            if result_data.get("status") == "ready":
                token = result_data["solution"]["token"]
                logger.info("FunCaptcha resuelto!")

                # Inyectar token en la página
                await page.evaluate(f"""
                    if (window.ArkoseEnforcement) {{
                        window.ArkoseEnforcement.setConfig({{data: {{token: "{token}"}}}});
                    }}
                    // Callback genérico
                    if (typeof captchaCallback === 'function') captchaCallback("{token}");
                    if (typeof verifyCallback === 'function') verifyCallback("{token}");
                """)
                await page.wait_for_timeout(3000)
                return True

            if result_data.get("status") == "failed":
                logger.error(f"CapSolver falló: {result_data}")
                return False

    logger.error("CapSolver timeout (120s)")
    return False


async def solve_recaptcha(page, page_content: str) -> bool:
    """Resuelve reCAPTCHA v2 usando CapSolver."""
    import re

    site_key_match = re.search(r'sitekey["\s:=]+["\']([a-zA-Z0-9_-]+)["\']', page_content, re.I)
    if not site_key_match:
        logger.error("No se pudo extraer sitekey de reCAPTCHA")
        return False

    site_key = site_key_match.group(1)
    website_url = page.url

    async with httpx.AsyncClient(timeout=120) as client:
        create_resp = await client.post(f"{CAPSOLVER_API_URL}/createTask", json={
            "appId": "6A47C22E-E45E-4023-A8D3-E498E2B475E6",
            "clientKey": CAPSOLVER_API_KEY,
            "task": {
                "type": "ReCaptchaV2TaskProxyLess",
                "websiteURL": website_url,
                "websiteKey": site_key,
            }
        })
        create_data = create_resp.json()
        if create_data.get("errorId", 1) != 0:
            logger.error(f"CapSolver error: {create_data}")
            return False

        task_id = create_data["taskId"]
        for _ in range(60):
            await asyncio.sleep(2)
            result_resp = await client.post(f"{CAPSOLVER_API_URL}/getTaskResult", json={
                "clientKey": CAPSOLVER_API_KEY,
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
    """Resuelve hCaptcha usando CapSolver."""
    import re

    site_key_match = re.search(r'sitekey["\s:=]+["\']([a-f0-9-]+)["\']', page_content, re.I)
    if not site_key_match:
        return False

    site_key = site_key_match.group(1)
    website_url = page.url

    async with httpx.AsyncClient(timeout=120) as client:
        create_resp = await client.post(f"{CAPSOLVER_API_URL}/createTask", json={
            "appId": "6A47C22E-E45E-4023-A8D3-E498E2B475E6",
            "clientKey": CAPSOLVER_API_KEY,
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
            result_resp = await client.post(f"{CAPSOLVER_API_URL}/getTaskResult", json={
                "clientKey": CAPSOLVER_API_KEY,
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
    Último recurso: toma screenshot del CAPTCHA y lo envía como imagen.
    Funciona para CAPTCHAs de texto/imagen simples.
    """
    import base64

    try:
        screenshot_bytes = await page.screenshot()
        image_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')

        async with httpx.AsyncClient(timeout=120) as client:
            create_resp = await client.post(f"{CAPSOLVER_API_URL}/createTask", json={
                "appId": "6A47C22E-E45E-4023-A8D3-E498E2B475E6",
                "clientKey": CAPSOLVER_API_KEY,
                "task": {
                    "type": "ImageToTextTask",
                    "body": image_b64,
                }
            })
            create_data = create_resp.json()
            if create_data.get("errorId", 1) != 0:
                logger.error(f"CapSolver screenshot error: {create_data}")
                return False

            task_id = create_data["taskId"]
            for _ in range(30):
                await asyncio.sleep(2)
                result_resp = await client.post(f"{CAPSOLVER_API_URL}/getTaskResult", json={
                    "clientKey": CAPSOLVER_API_KEY,
                    "taskId": task_id
                })
                result_data = result_resp.json()
                if result_data.get("status") == "ready":
                    text = result_data["solution"]["text"]
                    logger.info(f"CAPTCHA resuelto por screenshot: {text}")

                    # Intentar inyectar el texto en un input visible
                    try:
                        captcha_input = page.locator("input[type='text']").first
                        if await captcha_input.is_visible(timeout=2000):
                            await captcha_input.fill(text)
                            # Buscar botón de submit
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


# Necesario para las funciones async que usan asyncio.sleep
import asyncio
