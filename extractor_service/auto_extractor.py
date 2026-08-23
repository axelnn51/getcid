import os
import sys
import json
import urllib.parse
import time
import requests
import re
from core import DPoPEngine

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN = os.getenv("TELEGRAM_ADMIN_ID")
MS_EMAIL = os.getenv("MS_EMAIL")
MS_PASSWORD = os.getenv("MS_PASSWORD")
MS_ACCOUNTS = os.getenv("MS_ACCOUNTS")

if (not MS_EMAIL or not MS_PASSWORD) and MS_ACCOUNTS:
    try:
        first_account = MS_ACCOUNTS.split(',')[0]
        MS_EMAIL, MS_PASSWORD = first_account.split(':', 1)
    except Exception:
        pass

def send_telegram_alert(msg):
    if TELEGRAM_TOKEN and TELEGRAM_ADMIN:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_ADMIN, "text": msg, "parse_mode": "Markdown"})

def fetch_microsoft_code():
    import imaplib
    import email
    
    gmail_user = os.getenv("GMAIL_RECOVERY_EMAIL")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD")
    
    if not gmail_user or not gmail_pass:
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

def get_chrome_major_version():
    import subprocess
    try:
        process = subprocess.Popen(['google-chrome', '--version'], stdout=subprocess.PIPE)
        out, _ = process.communicate()
        match = re.search(r'\d+', out.decode('utf-8'))
        if match:
            return int(match.group(0))
    except Exception:
        pass
    return None

def get_cloudflare_url():
    try:
        with open("/tmp/cloudflared.log", "r") as f:
            log = f.read()
            match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", log)
            if match:
                return match.group(0)
    except:
        pass
    return "http://TU_IP:6080"

def extract_session():
    print("Iniciando Auto Extractor (undetected-chromedriver)...")
    send_telegram_alert("🔄 *Fase 1/3:* Iniciando navegador indetectable...")
    
    # Importar dentro para evitar fallos si se ejecuta el chequeo inicial
    from seleniumwire import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    
    engine = DPoPEngine()
    
    options = uc.ChromeOptions()
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    
    sw_options = {
        'disable_encoding': True
    }
    
    chrome_version = get_chrome_major_version()
    
    driver = uc.Chrome(
        options=options, 
        seleniumwire_options=sw_options,
        user_data_dir="/app/playwright_data",
        version_main=chrome_version
    )
    
    tokens_captured = {}
    captured_client_id = None
    
    def request_interceptor(request):
        if "common/oauth2/v2.0/token" in request.url.lower():
            if captured_client_id:
                request.abort()
                return
            
            dpop_proof = engine.generate_dpop_proof(request.method, request.url)
            request.headers["DPoP"] = dpop_proof
            
            if request.body:
                try:
                    body_str = request.body.decode('utf-8')
                    if "token_type=pop" not in body_str:
                        request.body = (body_str + "&token_type=pop").encode('utf-8')
                except:
                    pass
                    
    driver.request_interceptor = request_interceptor
    
    try:
        print("Navegando a la página de login...")
        send_telegram_alert("🔄 *Fase 2/3:* Navegando a Microsoft y enviando credenciales...")
        driver.get("https://visualsupport.microsoft.com/")
        time.sleep(3)
        
        # Llenar email
        try:
            email_input = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email']"))
            )
            print("Escribiendo email...")
            email_input.send_keys(MS_EMAIL)
            email_input.send_keys(u'\ue007') # Enter
            time.sleep(3)
        except:
            pass
            
        # Llenar password
        try:
            pass_input = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='password']"))
            )
            print("Escribiendo contraseña...")
            pass_input.send_keys(MS_PASSWORD)
            pass_input.send_keys(u'\ue007')
            time.sleep(3)
        except:
            pass
            
        # Stay signed in
        try:
            yes_btn = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "input[id='idSIButton9']"))
            )
            print("Haciendo click en 'Stay signed in'...")
            yes_btn.click()
        except:
            pass
            
    except Exception as e:
        print(f"Aviso en automatización: {e}")
        
    print("Esperando a capturar el token...")
    send_telegram_alert("🔄 *Fase 3/3:* Analizando tráfico de red para interceptar tokens...")
    
    # Revisar peticiones (timeout 25 segs)
    start_time = time.time()
    while time.time() - start_time < 25:
        for request in driver.requests:
            if "common/oauth2/v2.0/token" in request.url.lower() and request.response:
                if request.method == "POST":
                    try:
                        body_str = request.response.body.decode('utf-8')
                        data = json.loads(body_str)
                        if "refresh_token" in data:
                            post_data = request.body.decode('utf-8')
                            parsed = urllib.parse.parse_qs(post_data)
                            client_id = parsed.get("client_id", [""])[0]
                            if client_id and not captured_client_id:
                                print(f"✅ Token capturado: {client_id}")
                                captured_client_id = client_id
                                tokens_captured["refresh_token"] = data["refresh_token"]
                                tokens_captured["access_token"] = data.get("access_token")
                                tokens_captured["client_id"] = client_id
                    except:
                        pass
        if captured_client_id:
            break
        time.sleep(1)
        
    if not captured_client_id:
        print("Iniciando flujo de auto-verificación por IMAP...")
        try:
            # 1. Intentar hacer click en el primer Proof (ej. Email)
            try:
                print("Buscando opciones de Proof...")
                driver.execute_script("""
                    var proofs = document.querySelectorAll('div[data-bind*="selectProof"]');
                    if(proofs.length > 0) proofs[0].click();
                """)
                time.sleep(3)
            except Exception as e:
                print(f"Error click proof: {e}")
                
            # 2. Si pide confirmar el correo electrónico
            try:
                print("Buscando confirmación de correo...")
                recovery_email = os.getenv("GMAIL_RECOVERY_EMAIL", "")
                driver.execute_script(f"""
                    var conf = document.getElementById('idTxtBx_SAOTCS_ProofConfirmation');
                    var btn = document.getElementById('idSubmit_SAOTCS_SendCode');
                    if(conf && btn) {{
                        conf.value = '{recovery_email}';
                        conf.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        conf.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        btn.click();
                    }}
                """)
                time.sleep(3)
            except Exception as e:
                print(f"Error confirmación: {e}")
                
            # 3. Buscar campo de código
            try:
                print("Buscando input de código (idTxtBx_SAOTCC_OTC)...")
                code_input = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.ID, "idTxtBx_SAOTCC_OTC"))
                )
                send_telegram_alert("🔄 *Fase 3/3:* Recuperando código de seguridad desde Gmail (IMAP)...")
                print("Esperando 15s para que llegue el correo...")
                time.sleep(15)
                
                code = fetch_microsoft_code()
                if code:
                    send_telegram_alert(f"✅ Código interceptado: `{code}`. Inyectando en el navegador...")
                    driver.execute_script(f"""
                        var otc = document.getElementById('idTxtBx_SAOTCC_OTC');
                        var btn = document.getElementById('idSubmit_SAOTCC_Continue');
                        if(otc && btn) {{
                            otc.value = '{code}';
                            otc.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            otc.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            btn.click();
                        }}
                    """)
                    time.sleep(5)
                    
                    try:
                        yes_btn = WebDriverWait(driver, 3).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, "input[id='idSIButton9']"))
                        )
                        yes_btn.click()
                        time.sleep(3)
                    except:
                        pass
                else:
                    print("IMAP no devolvió ningún código válido.")
            except Exception as e:
                print(f"No se encontró input de código o falló inyección.")
                with open("/tmp/ms_error_page.html", "w") as f:
                    f.write(driver.page_source)
                print("Página de error guardada en /tmp/ms_error_page.html")
                
            # Volver a revisar las peticiones a ver si lo atrapamos
            for request in driver.requests:
                if "common/oauth2/v2.0/token" in request.url.lower() and request.response:
                    if request.method == "POST":
                        try:
                            body_str = request.response.body.decode('utf-8')
                            data = json.loads(body_str)
                            if "refresh_token" in data:
                                post_data = request.body.decode('utf-8')
                                parsed = urllib.parse.parse_qs(post_data)
                                client_id = parsed.get("client_id", [""])[0]
                                if client_id and not captured_client_id:
                                    print(f"✅ Token capturado tras auto-verificación: {client_id}")
                                    captured_client_id = client_id
                                    tokens_captured["refresh_token"] = data["refresh_token"]
                                    tokens_captured["access_token"] = data.get("access_token")
                                    tokens_captured["client_id"] = client_id
                        except:
                            pass
        except Exception as e:
            print(f"Error en flujo de auto-verificación: {e}")

    if not captured_client_id:
        cf_url = get_cloudflare_url()
        print("Posible bloqueo SMS detectado. Enviando alerta a Telegram...")
        send_telegram_alert(f"🚨 *GETCID Bot Esperando Verificación*\n\nEl servidor superó el sistema Anti-Bots, pero Microsoft requiere verificar la cuenta (posible código SMS o validación visual).\n\n👉 Entra a este enlace remoto y seguro desde tu celular (sin importar dónde estés) para resolverlo:\n\n{cf_url}\n\nEl bot te esperará indefinidamente...")
        
        while not captured_client_id:
            for request in driver.requests:
                if "common/oauth2/v2.0/token" in request.url.lower() and request.response:
                    if request.method == "POST":
                        try:
                            body_str = request.response.body.decode('utf-8')
                            data = json.loads(body_str)
                            if "refresh_token" in data:
                                post_data = request.body.decode('utf-8')
                                parsed = urllib.parse.parse_qs(post_data)
                                client_id = parsed.get("client_id", [""])[0]
                                if client_id and not captured_client_id:
                                    print(f"✅ Token capturado tras intervención: {client_id}")
                                    captured_client_id = client_id
                                    tokens_captured["refresh_token"] = data["refresh_token"]
                                    tokens_captured["access_token"] = data.get("access_token")
                                    tokens_captured["client_id"] = client_id
                        except:
                            pass
            time.sleep(2)
            
    print("Token capturado. Enviando sesión al backend principal...")
    
    cookies = driver.get_cookies()
    export_data = {
        "storage_state": {"cookies": cookies},
        "tokens_network": tokens_captured,
        "dpop_key": engine.get_pem_string()
    }
    
    try:
        resp = requests.post("http://getcid_backend:8000/api/update_session", json=export_data)
        print(f"Backend response: {resp.text}")
        send_telegram_alert("✅ *Token recuperado con éxito*\n\nEl servidor de GETCID vuelve a operar con normalidad.")
    except Exception as e:
        print(f"Error enviando al backend: {e}")
    
    time.sleep(2)
    driver.quit()

if __name__ == "__main__":
    if not MS_EMAIL or not MS_PASSWORD:
        print("ERROR: MS_EMAIL y MS_PASSWORD no definidos.")
        sys.exit(1)
    extract_session()
