"""
🔐 LOGIN MANUAL - Device Code Flow
===================================
Ejecuta este script UNA SOLA VEZ para autenticarte.

Pasos:
1. Ejecuta: python manual_login.py
2. Te dará un código (ej: "ABC-123")  
3. Abre https://microsoft.com/devicelogin en tu celular/PC
4. Pega el código e inicia sesión con tu cuenta Microsoft
5. ¡Listo! El token se guarda automáticamente (dura 90 días)

Después de esto, el servicio GETCID se auto-renueva solo.
NO necesitas CAPTCHAs, NO necesitas navegador, NO necesitas CapMonster.
"""

import asyncio
import httpx
import json
import time
import os
import sys

# Configuración
CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"  # Azure CLI (Native App → 90 días)
CLIENT_NAME = "Azure CLI"
SCOPES = "openid profile offline_access"

# Archivos de token (compatibles con el servicio existente)
PERSIST_DIR = "."
REFRESH_TOKEN_FILE = os.path.join(PERSIST_DIR, "ms_refresh_token.json")
TOKEN_CACHE_FILE = os.path.join(PERSIST_DIR, "ms_token.json")


def print_banner():
    print("\n" + "=" * 60)
    print("  🔐 GETCID - Login Manual (Device Code Flow)")
    print("  📅 Token válido por 90 días, se auto-renueva")
    print("  🚫 Sin CAPTCHAs, sin navegador, sin costos extra")
    print("=" * 60)


async def device_code_login():
    """Ejecuta el flujo completo de Device Code Flow."""
    
    print_banner()
    
    # Paso 1: Solicitar código de dispositivo
    print("\n⏳ Solicitando código de dispositivo a Microsoft...")
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode",
            data={
                "client_id": CLIENT_ID,
                "scope": SCOPES
            }
        )
        
        if resp.status_code != 200:
            print(f"\n❌ Error solicitando código: {resp.status_code}")
            print(f"   Respuesta: {resp.text[:300]}")
            return False
        
        data = resp.json()
    
    user_code = data["user_code"]
    verification_uri = data.get("verification_uri", "https://microsoft.com/devicelogin")
    device_code = data["device_code"]
    expires_in = data.get("expires_in", 900)
    interval = data.get("interval", 5)
    
    # Paso 2: Mostrar instrucciones al usuario
    print("\n" + "=" * 60)
    print(f"  📋 TU CÓDIGO: {user_code}")
    print(f"  🌐 URL: {verification_uri}")
    print("=" * 60)
    print(f"\n  👉 INSTRUCCIONES:")
    print(f"     1. Abre en tu navegador: {verification_uri}")
    print(f"     2. Escribe el código: {user_code}")
    print(f"     3. Inicia sesión con tu cuenta Microsoft")
    print(f"     4. Acepta los permisos")
    print(f"\n  ⏱️  Tienes {expires_in // 60} minutos para completar")
    print(f"     Este script esperará automáticamente...\n")
    
    # Paso 3: Polling - esperar a que el usuario complete el login
    start_time = time.time()
    attempt = 0
    
    async with httpx.AsyncClient(timeout=30) as client:
        while time.time() - start_time < expires_in:
            await asyncio.sleep(interval)
            attempt += 1
            
            elapsed = int(time.time() - start_time)
            print(f"\r  ⏳ Esperando que inicies sesión... ({elapsed}s)", end="", flush=True)
            
            try:
                resp = await client.post(
                    "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                    data={
                        "client_id": CLIENT_ID,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code
                    }
                )
                
                result = resp.json()
                
                if resp.status_code == 200 and "access_token" in result:
                    # ¡ÉXITO!
                    print(f"\r  ✅ ¡LOGIN EXITOSO!                              ")
                    return await _save_tokens(result)
                
                error = result.get("error", "")
                
                if "authorization_pending" in error:
                    continue  # Todavía esperando
                
                if "authorization_declined" in error:
                    print(f"\r  ❌ Rechazaste la autorización.                   ")
                    return False
                
                if "expired_token" in error:
                    print(f"\r  ❌ El código expiró. Ejecuta el script de nuevo.  ")
                    return False
                
                if "slow_down" in error:
                    interval = min(interval + 2, 15)
                    continue
                
                # Error desconocido
                print(f"\r  ❌ Error: {result.get('error_description', error)[:100]}")
                return False
                
            except Exception as e:
                print(f"\r  ⚠️ Error de conexión: {e}. Reintentando...", end="")
                continue
    
    print(f"\r  ❌ Timeout: no completaste el login en {expires_in // 60} minutos.")
    return False


async def _save_tokens(token_data: dict) -> bool:
    """Guarda los tokens en disco."""
    
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)
    
    if not access_token:
        print("  ❌ No se recibió access token.")
        return False
    
    # Guardar access token
    with open(TOKEN_CACHE_FILE, "w") as f:
        json.dump({
            "token": access_token,
            "expires_at": time.time() + expires_in - 120
        }, f, indent=2)
    
    print(f"\n  📁 Access token guardado en: {TOKEN_CACHE_FILE}")
    print(f"     Expira en: {expires_in // 60} minutos")
    
    # Guardar refresh token
    if refresh_token:
        with open(REFRESH_TOKEN_FILE, "w") as f:
            json.dump({
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
                "scopes": SCOPES,
                "token_type": "native_app",
                "token_type_label": "Native App (90 días)",
                "max_lifetime_days": 90,
                "saved_at": time.time(),
                "saved_at_readable": time.strftime('%Y-%m-%d %H:%M:%S'),
                "last_refreshed_at": time.time()
            }, f, indent=2)
        
        print(f"  📁 Refresh token guardado en: {REFRESH_TOKEN_FILE}")
        print(f"     Válido por: ~90 días (se auto-renueva)")
        
        print("\n" + "=" * 60)
        print("  🎉 ¡TODO LISTO!")
        print("  ")
        print("  Tu servicio GETCID ahora puede:")
        print("  ✅ Generar access tokens automáticamente")
        print("  ✅ Auto-renovarse sin intervención manual")
        print("  ✅ Funcionar 24/7 sin CAPTCHAs")
        print("  ")
        print("  El token se renueva cada hora automáticamente.")
        print("  Solo necesitas repetir este proceso cada 90 días")
        print("  (o cuando recibas una alerta por Telegram).")
        print("=" * 60 + "\n")
        return True
    else:
        print("  ⚠️ No se recibió refresh token. El access token solo durará ~1 hora.")
        print("     Intenta de nuevo o usa un scope diferente.")
        return False


def main():
    # Verificar si ya hay un token válido
    if os.path.exists(REFRESH_TOKEN_FILE):
        try:
            with open(REFRESH_TOKEN_FILE, "r") as f:
                data = json.load(f)
            saved_at = data.get("saved_at", 0)
            age_days = (time.time() - saved_at) / 86400
            max_days = data.get("max_lifetime_days", 90)
            remaining = max_days - age_days
            
            if remaining > 0:
                print(f"\n⚠️ Ya tienes un refresh token guardado:")
                print(f"   Tipo: {data.get('token_type_label', '?')}")
                print(f"   Guardado: {data.get('saved_at_readable', '?')}")
                print(f"   Días restantes: {int(remaining)}")
                print(f"\n   ¿Quieres reemplazarlo? (s/n): ", end="")
                
                answer = input().strip().lower()
                if answer != "s" and answer != "si" and answer != "sí" and answer != "y":
                    print("   Cancelado. El token actual sigue vigente.")
                    return
        except:
            pass
    
    success = asyncio.run(device_code_login())
    
    if not success:
        print("\n💡 Si falla, verifica:")
        print("   1. Que tu cuenta Microsoft esté activa")
        print("   2. Que tengas conexión a internet")
        print("   3. Que hayas iniciado sesión correctamente en el navegador")
        sys.exit(1)


if __name__ == "__main__":
    main()
