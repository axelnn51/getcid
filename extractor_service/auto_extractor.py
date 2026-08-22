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

def send_telegram_alert(msg):
    if TELEGRAM_TOKEN and TELEGRAM_ADMIN:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_ADMIN, "text": msg, "parse_mode": "Markdown"})

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
    
    driver = uc.Chrome(
        options=options, 
        seleniumwire_options=sw_options,
        user_data_dir="/app/playwright_data"
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
