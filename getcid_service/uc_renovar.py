"""
Módulo alternativo usando SeleniumBase (Undetected Chromedriver)
Para evadir Arkose Labs (Microsoft CAPTCHA Shadow Ban)
"""
import os
import time
import json
import logging
import asyncio
from dotenv import load_dotenv
from seleniumbase import SB

load_dotenv()
logger = logging.getLogger("UC_Renovar")

MS_EMAIL = os.getenv("MS_EMAIL")
MS_PASSWORD = os.getenv("MS_PASSWORD")
GMAIL_RECOVERY_EMAIL = os.getenv("GMAIL_RECOVERY_EMAIL")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

def run_uc_login():
    """Ejecuta el flujo de login de Microsoft usando SeleniumBase UC Mode"""
    print("🚀 Iniciando SeleniumBase UC Mode para evadir Arkose Labs...")
    
    with SB(uc=True, headless=True, incognito=True) as sb:
        print("🌐 Navegando a visualsupport.microsoft.com...")
        sb.uc_open_with_reconnect("https://visualsupport.microsoft.com/", 5)
        
        # 1. Click en Sign In si existe
        if sb.is_element_visible("button#signIn"):
            print("🔘 Clickeando Sign In...")
            sb.uc_click("button#signIn")
        
        # 2. Rellenar Email
        print("📧 Esperando input de email...")
        sb.type("input[type='email']", MS_EMAIL)
        sb.uc_click("input[type='submit']")
        
        time.sleep(3)
        
        # 3. Manejo de Contraseña o Código
        if sb.is_element_visible("input[type='password']"):
            print("🔑 Ingresando contraseña...")
            sb.type("input[type='password']", MS_PASSWORD)
            sb.uc_click("input[type='submit']")
        
        # Ojo: la lógica de 2FA/IMAP y Gemini para UC Mode requiere más desarrollo
        # porque SB es síncrono. Este es el andamiaje principal de la Fase 2.
        
        time.sleep(10)
        
        print("💾 Extrayendo Tokens de Storage...")
        session_storage = sb.execute_script("return window.sessionStorage;")
        local_storage = sb.execute_script("return window.localStorage;")
        
        found_refresh = False
        for storage in [session_storage, local_storage]:
            if not storage: continue
            for k, v in storage.items():
                if "refreshtoken" in k.lower():
                    try:
                        parsed = json.loads(v)
                        if "secret" in parsed:
                            refresh_token = parsed["secret"]
                            client_id = parsed.get("client_id")
                            print(f"🎯 Refresh Token Capturado: {refresh_token[:15]}...")
                            
                            # Guardar Refresh Token
                            base_dir = "/app/persist" if os.path.exists("/app/persist") else "."
                            with open(os.path.join(base_dir, "ms_refresh_token.json"), "w") as f:
                                json.dump({
                                    "refresh_token": refresh_token,
                                    "client_id": client_id
                                }, f)
                            found_refresh = True
                    except:
                        pass
        
        if found_refresh:
            print("✅ ¡Login Exitoso usando SeleniumBase!")
            return True
        else:
            print("❌ No se pudo capturar el token.")
            return False

if __name__ == "__main__":
    run_uc_login()
