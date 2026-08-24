"""
GETCID Auto-Extractor v3.0 — nodriver + CDP Nativo
===================================================
Reemplaza selenium-wire por nodriver (sucesor de undetected-chromedriver).
Intercepta la red con Chrome DevTools Protocol (Fetch domain) sin proxy MITM,
preservando el TLS Fingerprint y HTTP/2 nativos de Chrome.

Microsoft Azure WAF no puede detectar esta configuración porque:
1. No hay proxy local (no hay degradación HTTP/2 → HTTP/1.1)
2. No hay chromedriver binario (no hay navigator.webdriver)
3. El navegador corre en modo headed con Xvfb (parece monitor real)
4. Se persisten cookies con user_data_dir (parece usuario recurrente)
"""

import os
import sys
import json
import urllib.parse
import time
import asyncio
import base64
import random
import re
import requests

from core import DPoPEngine

# ─── Configuración ───────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN = os.getenv("TELEGRAM_ADMIN_ID")
MS_EMAIL = os.getenv("MS_EMAIL")
MS_PASSWORD = os.getenv("MS_PASSWORD")
MS_ACCOUNTS = os.getenv("MS_ACCOUNTS")

# Fallback: si no hay MS_EMAIL/MS_PASSWORD, parsear de MS_ACCOUNTS
if (not MS_EMAIL or not MS_PASSWORD) and MS_ACCOUNTS:
    try:
        first_account = MS_ACCOUNTS.split(',')[0]
        MS_EMAIL, MS_PASSWORD = first_account.split(':', 1)
    except Exception:
        pass

USER_DATA_DIR = "/app/playwright_data"
BACKEND_URL = "http://getcid_backend:8000"


# ─── Utilidades ──────────────────────────────────────────────────

def send_telegram_alert(msg):
    """Enviar alerta a Telegram."""
    if TELEGRAM_TOKEN and TELEGRAM_ADMIN:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, json={
                "chat_id": TELEGRAM_ADMIN,
                "text": msg,
                "parse_mode": "Markdown"
            }, timeout=10)
        except Exception as e:
            print(f"Error enviando alerta Telegram: {e}")


def fetch_microsoft_code():
    """Extraer código de verificación de Microsoft desde Gmail vía IMAP."""
    import imaplib
    import email

    gmail_user = os.getenv("GMAIL_RECOVERY_EMAIL")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD")

    if not gmail_user or not gmail_pass:
        print("IMAP: No hay credenciales de Gmail configuradas.")
        return None

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(gmail_user, gmail_pass)
        mail.select("inbox")

        status, messages = mail.search(None, 'FROM', '"Microsoft account team"')
        if status == "OK" and messages[0]:
            latest_id = messages[0].split()[-1]
            status, data = mail.fetch(latest_id, '(RFC822)')
            if status == "OK":
                msg = email.message_from_bytes(data[0][1])
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode()
                            break
                else:
                    body = msg.get_payload(decode=True).decode()

                match = re.search(r'\b\d{6,7}\b', body)
                if match:
                    code = match.group(0)
                    mail.logout()
                    return code
        mail.logout()
    except Exception as e:
        print(f"Error IMAP: {e}")
    return None


def get_cloudflare_url():
    """Obtener URL del túnel de Cloudflare desde su log."""
    try:
        with open("/tmp/cloudflared.log", "r") as f:
            log = f.read()
            match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", log)
            if match:
                return match.group(0)
    except Exception:
        pass
    return "http://TU_IP:6080"


async def human_delay(min_s=0.5, max_s=2.0):
    """Delay aleatorio para simular comportamiento humano."""
    delay = random.uniform(min_s, max_s)
    await asyncio.sleep(delay)


# ─── Extractor Principal ─────────────────────────────────────────

async def extract_session():
    """
    Proceso principal de extracción de tokens usando nodriver + CDP.
    """
    # Importar nodriver aquí para evitar fallos si se ejecuta el chequeo inicial
    import nodriver as uc
    from nodriver.cdp import fetch, network

    print("=" * 60)
    print("GETCID Auto-Extractor v3.0 (nodriver + CDP Nativo)")
    print("=" * 60)
    send_telegram_alert("🔄 *Fase 1/3:* Iniciando navegador indetectable (nodriver)...")

    # Motor criptográfico DPoP
    engine = DPoPEngine()

    # Estado de captura
    tokens_captured = {}
    captured_client_id = None

    # ─── Iniciar navegador ────────────────────────────────────
    print("Arrancando navegador Chrome con nodriver (sin chromedriver)...")

    browser = await uc.start(
        headless=False,  # Headed + Xvfb para máxima evasión
        sandbox=False,    # Necesario en Docker
        user_data_dir=USER_DATA_DIR,
        browser_args=[
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
        ]
    )

    tab = browser.main_tab

    # ─── Configurar interceptor CDP (Fetch domain) ───────────
    print("Configurando interceptor CDP (Fetch.RequestPaused)...")

    async def on_request_paused(event: fetch.RequestPaused):
        """
        Handler CDP unificado que intercepta tanto REQUEST como RESPONSE stages.
        
        CDP Fetch domain envía RequestPaused para ambos stages:
        - REQUEST stage: event.response_status_code es None → inyectar DPoP
        - RESPONSE stage: event.response_status_code tiene valor → leer tokens
        """
        nonlocal captured_client_id, tokens_captured

        request_url = event.request.url
        request_method = event.request.method
        is_token_url = "common/oauth2/v2.0/token" in request_url.lower() and request_method == "POST"

        # ═══ RESPONSE STAGE: Leer tokens de la respuesta ═══
        if event.response_status_code is not None:
            if is_token_url and not captured_client_id:
                try:
                    response_body = await tab.send(fetch.get_response_body(request_id=event.request_id))
                    body_str = response_body.body
                    if response_body.base64_encoded:
                        body_str = base64.b64decode(body_str).decode('utf-8')

                    data = json.loads(body_str)

                    if "refresh_token" in data:
                        # Extraer client_id del POST data original
                        post_data = event.request.post_data or ""
                        parsed = urllib.parse.parse_qs(post_data)
                        client_id = parsed.get("client_id", [""])[0]

                        if client_id:
                            print(f"✅ ¡TOKEN CAPTURADO VÍA CDP! client_id: {client_id}")
                            captured_client_id = client_id
                            tokens_captured["refresh_token"] = data["refresh_token"]
                            tokens_captured["access_token"] = data.get("access_token")
                            tokens_captured["client_id"] = client_id
                            send_telegram_alert(f"✅ *Token interceptado por CDP*\nClient: `{client_id[:16]}...`")
                        else:
                            print("⚠️ Token encontrado pero sin client_id en el POST.")

                except Exception as e:
                    print(f"Error leyendo respuesta de token: {e}")

            # Siempre continuar la respuesta para que el navegador la reciba
            try:
                await tab.send(fetch.continue_response(request_id=event.request_id))
            except Exception:
                try:
                    await tab.send(fetch.continue_request(request_id=event.request_id))
                except Exception:
                    pass
            return

        # ═══ REQUEST STAGE: Inyectar DPoP en requests OAuth2 ═══
        if is_token_url:
            # Si ya capturamos un token, bloquear requests posteriores
            if captured_client_id:
                print("🛡️ Bloqueando request de token posterior (ya capturado).")
                try:
                    await tab.send(fetch.fail_request(
                        request_id=event.request_id,
                        reason=network.ErrorReason.BLOCKED_BY_CLIENT
                    ))
                except Exception:
                    pass
                return

            print("🔐 Interceptando request OAuth2 → Inyectando DPoP...")

            # Generar DPoP proof para esta request
            dpop_proof = engine.generate_dpop_proof(request_method, request_url)

            # Preparar headers modificados (añadir DPoP)
            new_headers = []
            if event.request.headers:
                try:
                    headers_dict = event.request.headers.to_json()
                except AttributeError:
                    # Fallback: si headers es ya un dict
                    headers_dict = dict(event.request.headers) if event.request.headers else {}
                
                for name, value in headers_dict.items():
                    if name.lower() != "dpop":
                        new_headers.append(fetch.HeaderEntry(name=name, value=str(value)))

            # Añadir nuestro DPoP
            new_headers.append(fetch.HeaderEntry(name="DPoP", value=dpop_proof))

            # Modificar POST body para añadir token_type=pop
            post_data_b64 = None
            if event.request.post_data:
                original_body = event.request.post_data
                if "token_type=pop" not in original_body:
                    original_body += "&token_type=pop"
                post_data_b64 = base64.b64encode(original_body.encode('utf-8')).decode('utf-8')

            # Continuar la request con nuestras modificaciones
            try:
                await tab.send(fetch.continue_request(
                    request_id=event.request_id,
                    headers=new_headers,
                    post_data=post_data_b64
                ))
            except Exception as e:
                print(f"Error continuando request OAuth2: {e}")
                try:
                    await tab.send(fetch.continue_request(request_id=event.request_id))
                except Exception:
                    pass
        else:
            # Para todas las demás requests, dejar pasar sin modificar
            try:
                await tab.send(fetch.continue_request(request_id=event.request_id))
            except Exception:
                pass

    # Registrar handler CDP único (maneja tanto REQUEST como RESPONSE stages)
    tab.add_handler(fetch.RequestPaused, on_request_paused)

    # Habilitar Fetch domain para interceptar requests Y respuestas
    # Patrón: solo interceptar requests al dominio de OAuth2 de Microsoft
    await tab.send(fetch.enable(
        patterns=[
            fetch.RequestPattern(
                url_pattern="*oauth2/v2.0/token*",
                request_stage=fetch.RequestStage.REQUEST
            ),
            fetch.RequestPattern(
                url_pattern="*oauth2/v2.0/token*",
                request_stage=fetch.RequestStage.RESPONSE
            ),
        ]
    ))


    print("✅ Interceptor CDP configurado. Patrón: *oauth2/v2.0/token*")

    # ─── Navegar a Microsoft ──────────────────────────────────
    print("Navegando a visualsupport.microsoft.com...")
    send_telegram_alert("🔄 *Fase 2/3:* Navegando a Microsoft y enviando credenciales...")

    try:
        await tab.get("https://visualsupport.microsoft.com/")
    except Exception as e:
        print(f"Aviso al navegar: {e}")

    await human_delay(3.0, 5.0)

    # Buscar y clickear botón "Comencemos"
    try:
        print("Buscando botón 'Comencemos'...")
        btn = await tab.select("button.ms-Button--primary", timeout=15)
        if btn:
            print("Botón encontrado. Haciendo click real...")
            await btn.scroll_into_view()
            await human_delay(0.5, 1.0)
            await btn.mouse_click()
            await human_delay(2.0, 4.0)
    except Exception as e:
        print(f"Aviso al clickear Comencemos (puede que ya esté en login): {e}")

    # ─── Automatizar login ────────────────────────────────────
    # Paso 1: Email
    try:
        print("Buscando campo de email...")
        email_input = await tab.find("input[type='email']", timeout=120)
        if email_input:
            print(f"Escribiendo email: {MS_EMAIL}")
            await email_input.clear_input()
            await human_delay(0.3, 0.8)
            await email_input.send_keys(MS_EMAIL)
            await human_delay(0.5, 1.0)
            # Presionar Enter o buscar botón de siguiente
            await email_input.send_keys("\n")
            await human_delay(2.0, 3.0)
    except Exception as e:
        print(f"Aviso en campo email: {e}")

    # Paso 2: Password
    try:
        print("Buscando campo de contraseña...")
        pass_input = await tab.find("input[type='password']", timeout=60)
        if pass_input:
            print("Escribiendo contraseña...")
            await pass_input.clear_input()
            await human_delay(0.3, 0.8)
            await pass_input.send_keys(MS_PASSWORD)
            await human_delay(0.5, 1.0)
            await pass_input.send_keys("\n")
            await human_delay(2.0, 4.0)
    except Exception as e:
        print(f"Aviso en campo password: {e}")

    # Paso 3: Stay signed in
    try:
        print("Buscando botón 'Stay signed in'...")
        yes_btn = await tab.find("#idSIButton9", timeout=10)
        if yes_btn:
            print("Haciendo click en 'Stay signed in'...")
            await human_delay(0.5, 1.0)
            await yes_btn.click()
            await human_delay(2.0, 3.0)
    except Exception as e:
        print(f"Aviso en 'Stay signed in': {e}")

    # ─── Esperar captura del token ────────────────────────────
    print("Esperando interceptar el token vía CDP...")
    send_telegram_alert("🔄 *Fase 3/3:* Esperando captura de tokens vía CDP...")

    # Buscar botón "Let's Get Started" periódicamente y revisar sessionStorage
    start_time = time.time()
    while time.time() - start_time < 30 and not captured_client_id:


        # Fallback: intentar leer tokens desde sessionStorage
        if not captured_client_id:
            try:
                storage_result = await tab.evaluate("""
                    (function() {
                        try {
                            var keys = Object.keys(sessionStorage);
                            var result = {};
                            for (var i = 0; i < keys.length; i++) {
                                var key = keys[i];
                                if (key.toLowerCase().indexOf('refreshtoken') !== -1 ||
                                    key.toLowerCase().indexOf('accesstoken') !== -1) {
                                    var val = JSON.parse(sessionStorage.getItem(key));
                                    if (val && val.credentialType === 'RefreshToken') {
                                        result.refresh_token = val.secret;
                                        result.client_id = val.clientId;
                                    } else if (val && val.credentialType === 'AccessToken') {
                                        result.access_token = val.secret;
                                    }
                                }
                            }
                            if (result.refresh_token && result.client_id) {
                                return JSON.stringify(result);
                            }
                        } catch(e) {}
                        return null;
                    })()
                """)

                if storage_result and storage_result != "null":
                    storage_data = json.loads(storage_result)
                    if "refresh_token" in storage_data and "client_id" in storage_data:
                        print(f"✅ Token capturado desde sessionStorage: {storage_data['client_id']}")
                        captured_client_id = storage_data["client_id"]
                        tokens_captured["refresh_token"] = storage_data["refresh_token"]
                        tokens_captured["access_token"] = storage_data.get("access_token")
                        tokens_captured["client_id"] = captured_client_id
                        send_telegram_alert(f"✅ *Token capturado (sessionStorage)*\nClient: `{captured_client_id[:16]}...`")
            except Exception:
                pass

        if not captured_client_id:
            await asyncio.sleep(1)

    # ─── Si no se capturó, intentar flujo de auto-verificación IMAP ───
    if not captured_client_id:
        print("Token no capturado aún. Verificando si Microsoft pide 2FA...")

        try:
            # 1. Buscar opciones de Proof (verificación)
            try:
                print("Buscando opciones de Proof...")
                await tab.evaluate("""
                    var proofs = document.querySelectorAll('div[data-bind*="selectProof"]');
                    if(proofs.length > 0) proofs[0].click();
                """)
                await human_delay(2.0, 3.0)
            except Exception as e:
                print(f"Aviso proof: {e}")

            # 2. Si pide confirmar el correo electrónico
            try:
                recovery_email = os.getenv("GMAIL_RECOVERY_EMAIL", "")
                print(f"Intentando confirmar correo de recuperación: {recovery_email}")
                await tab.evaluate(f"""
                    var conf = document.getElementById('idTxtBx_SAOTCS_ProofConfirmation');
                    var btn = document.getElementById('idSubmit_SAOTCS_SendCode');
                    if(conf && btn) {{
                        conf.value = '{recovery_email}';
                        conf.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        conf.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        btn.click();
                    }}
                """)
                await human_delay(2.0, 3.0)
            except Exception as e:
                print(f"Aviso confirmación email: {e}")

            # 3. Buscar campo de código de verificación
            try:
                code_input_exists = await tab.evaluate("""
                    document.getElementById('idTxtBx_SAOTCC_OTC') !== null
                """)

                if code_input_exists:
                    send_telegram_alert("🔄 *Fase 3/3:* Recuperando código de seguridad desde Gmail (IMAP)...")
                    print("Campo de código detectado. Esperando 15s para que llegue el correo...")
                    await asyncio.sleep(15)

                    code = fetch_microsoft_code()
                    if code:
                        send_telegram_alert(f"✅ Código interceptado: `{code}`. Inyectando en el navegador...")
                        print(f"Código IMAP obtenido: {code}. Inyectando...")

                        await tab.evaluate(f"""
                            var otc = document.getElementById('idTxtBx_SAOTCC_OTC');
                            var btn = document.getElementById('idSubmit_SAOTCC_Continue');
                            if(otc && btn) {{
                                otc.value = '{code}';
                                otc.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                otc.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                btn.click();
                            }}
                        """)
                        await human_delay(4.0, 6.0)

                        # Stay signed in después del 2FA
                        try:
                            yes_btn = await tab.find("#idSIButton9", timeout=5)
                            if yes_btn:
                                await yes_btn.click()
                                await human_delay(2.0, 3.0)
                        except Exception:
                            pass
                    else:
                        print("IMAP no devolvió ningún código válido.")
                else:
                    print("No se encontró campo de código de verificación.")
            except Exception as e:
                print(f"Aviso en flujo de verificación: {e}")

        except Exception as e:
            print(f"Error en flujo de auto-verificación: {e}")

        # Dar más tiempo para capturar token después del 2FA
        extra_wait_start = time.time()
        while time.time() - extra_wait_start < 20 and not captured_client_id:
            # Intentar sessionStorage de nuevo
            try:
                storage_result = await tab.evaluate("""
                    (function() {
                        try {
                            var keys = Object.keys(sessionStorage);
                            var result = {};
                            for (var i = 0; i < keys.length; i++) {
                                var key = keys[i];
                                if (key.toLowerCase().indexOf('refreshtoken') !== -1 ||
                                    key.toLowerCase().indexOf('accesstoken') !== -1) {
                                    var val = JSON.parse(sessionStorage.getItem(key));
                                    if (val && val.credentialType === 'RefreshToken') {
                                        result.refresh_token = val.secret;
                                        result.client_id = val.clientId;
                                    } else if (val && val.credentialType === 'AccessToken') {
                                        result.access_token = val.secret;
                                    }
                                }
                            }
                            if (result.refresh_token && result.client_id) {
                                return JSON.stringify(result);
                            }
                        } catch(e) {}
                        return null;
                    })()
                """)

                if storage_result and storage_result != "null":
                    storage_data = json.loads(storage_result)
                    if "refresh_token" in storage_data and "client_id" in storage_data:
                        print(f"✅ Token capturado desde sessionStorage tras 2FA: {storage_data['client_id']}")
                        captured_client_id = storage_data["client_id"]
                        tokens_captured["refresh_token"] = storage_data["refresh_token"]
                        tokens_captured["access_token"] = storage_data.get("access_token")
                        tokens_captured["client_id"] = captured_client_id
            except Exception:
                pass

            # Click en Let's Get Started
            try:
                await tab.evaluate("""
                    var btns = document.querySelectorAll('button');
                    for(var i=0; i<btns.length; i++){
                        if(btns[i].innerText && btns[i].innerText.includes("Started")) {
                            btns[i].click();
                        }
                    }
                """)
            except Exception:
                pass

            await asyncio.sleep(2)

    # ─── Último recurso: Cloudflare VNC ───────────────────────
    if not captured_client_id:
        cf_url = get_cloudflare_url()
        if not cf_url.startswith("Error"):
            cf_url += "/vnc.html"
        print("⚠️ Token no capturado automáticamente. Enviando enlace Cloudflare VNC a Telegram...")
        send_telegram_alert(
            f"🚨 *GETCID Bot Esperando Verificación*\n\n"
            f"El navegador no logró capturar el token automáticamente. "
            f"Puede que Microsoft requiera verificación manual.\n\n"
            f"👉 Entra a este enlace remoto y seguro desde tu celular:\n\n"
            f"{cf_url}\n\n"
            f"El bot te esperará indefinidamente..."
        )

        # Esperar indefinidamente hasta capturar el token (intervención humana)
        while not captured_client_id:
            # Intentar sessionStorage continuamente
            try:
                storage_result = await tab.evaluate("""
                    (function() {
                        try {
                            var keys = Object.keys(sessionStorage);
                            var result = {};
                            for (var i = 0; i < keys.length; i++) {
                                var key = keys[i];
                                if (key.toLowerCase().indexOf('refreshtoken') !== -1 ||
                                    key.toLowerCase().indexOf('accesstoken') !== -1) {
                                    var val = JSON.parse(sessionStorage.getItem(key));
                                    if (val && val.credentialType === 'RefreshToken') {
                                        result.refresh_token = val.secret;
                                        result.client_id = val.clientId;
                                    } else if (val && val.credentialType === 'AccessToken') {
                                        result.access_token = val.secret;
                                    }
                                }
                            }
                            if (result.refresh_token && result.client_id) {
                                return JSON.stringify(result);
                            }
                        } catch(e) {}
                        return null;
                    })()
                """)

                if storage_result and storage_result != "null":
                    storage_data = json.loads(storage_result)
                    if "refresh_token" in storage_data and "client_id" in storage_data:
                        print(f"✅ Token capturado desde sessionStorage tras intervención: {storage_data['client_id']}")
                        captured_client_id = storage_data["client_id"]
                        tokens_captured["refresh_token"] = storage_data["refresh_token"]
                        tokens_captured["access_token"] = storage_data.get("access_token")
                        tokens_captured["client_id"] = captured_client_id
            except Exception:
                pass

            await asyncio.sleep(3)

    # ─── Enviar tokens al backend ─────────────────────────────
    print("=" * 60)
    print(f"✅ TOKEN CAPTURADO EXITOSAMENTE")
    print(f"   Client ID: {captured_client_id}")
    print(f"   Refresh Token: {tokens_captured.get('refresh_token', '')[:30]}...")
    print("=" * 60)

    export_data = {
        "storage_state": {"cookies": []},
        "tokens_network": tokens_captured,
        "dpop_key": engine.get_pem_string()
    }

    # Intentar extraer cookies del navegador
    try:
        cookies_result = await tab.evaluate("""
            document.cookie
        """)
        if cookies_result:
            print(f"Cookies capturadas: {len(cookies_result)} caracteres")
    except Exception:
        pass

    # Enviar al backend
    try:
        print(f"Enviando sesión al backend ({BACKEND_URL}/api/update_session)...")
        resp = requests.post(
            f"{BACKEND_URL}/api/update_session",
            json=export_data,
            timeout=15
        )
        print(f"Backend response: {resp.status_code} - {resp.text}")
        send_telegram_alert("✅ *Token recuperado con éxito*\n\nEl servidor de GETCID vuelve a operar con normalidad.")
    except Exception as e:
        print(f"Error enviando al backend: {e}")
        send_telegram_alert(f"⚠️ Token capturado pero error al enviar al backend: {e}")

    # Cerrar navegador
    await asyncio.sleep(2)
    try:
        browser.stop()
    except Exception:
        pass

    print("Auto-Extractor finalizado exitosamente.")


# ─── Entry Point ──────────────────────────────────────────────────

def main():
    if not MS_EMAIL or not MS_PASSWORD:
        print("ERROR: MS_EMAIL y MS_PASSWORD no definidos.")
        sys.exit(1)
    
    print(f"Email: {MS_EMAIL}")
    print(f"Password: {'*' * len(MS_PASSWORD)}")
    
    asyncio.run(extract_session())


if __name__ == "__main__":
    main()
