"""
Renovador automático de tokens usando Microsoft Refresh Token.
100% gratis, sin CAPTCHA, sin navegador.

Flujo:
1. El usuario se loguea UNA VEZ (localmente o vía Device Code Flow)
2. Se captura el Refresh Token
3. El servidor usa el Refresh Token para generar Access Tokens nuevos
4. Cada refresh también renueva el Refresh Token → cadena extendida

IMPORTANTE: La duración real del refresh token depende del tipo de client_id:
- SPA (ej: visualsupport) → 24 horas MÁXIMO (hard limit de Microsoft)
- Native App (ej: Azure CLI) → 90 días (sliding window)
"""
import httpx
import json
import time
import os
import logging

logger = logging.getLogger("TokenRefresher")

# Usar directorio persistente si existe (Docker volume)
PERSIST_DIR = "/app/persist" if os.path.isdir("/app/persist") else "."
REFRESH_TOKEN_FILE = os.path.join(PERSIST_DIR, "ms_refresh_token.json")
TOKEN_CACHE_FILE = "ms_token.json"  # Este puede ser efímero, se regenera con refresh

# Client IDs conocidos como SPA (24h limit)
SPA_CLIENT_IDS = [
    "2b217cec",  # visualsupport.microsoft.com (prefijo)
]

# Client IDs conocidos como Native App (90 días)
NATIVE_CLIENT_IDS = [
    "04b07795",  # Azure CLI (prefijo)
    "d3590ed6",  # Microsoft Office (prefijo)
]


def _detect_token_type(client_id: str) -> dict:
    """Detecta el tipo de token basado en el client_id."""
    prefix = client_id[:8] if client_id else ""

    if any(prefix.startswith(spa) for spa in SPA_CLIENT_IDS):
        return {
            "type": "spa",
            "label": "SPA (24h max)",
            "max_lifetime_hours": 24,
            "max_lifetime_days": 1,
            "warning": "Los tokens SPA tienen un límite duro de 24 horas. El proactive refresh los mantiene activos."
        }

    if any(prefix.startswith(native) for native in NATIVE_CLIENT_IDS):
        return {
            "type": "native_app",
            "label": "Native App (90 días)",
            "max_lifetime_hours": 90 * 24,
            "max_lifetime_days": 90,
            "warning": None
        }

    return {
        "type": "unknown",
        "label": f"Desconocido ({prefix}...)",
        "max_lifetime_hours": 24,  # Asumir worst case
        "max_lifetime_days": 1,
        "warning": "Tipo de client_id no reconocido. Asumiendo límite de 24h por seguridad."
    }


async def refresh_access_token() -> str:
    """
    Usa el Refresh Token guardado para obtener un nuevo Access Token.
    Retorna el access token o None si falla.
    """
    if not os.path.exists(REFRESH_TOKEN_FILE):
        logger.warning("No hay refresh token guardado.")
        return None

    try:
        with open(REFRESH_TOKEN_FILE, 'r') as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"Error leyendo refresh token: {e}")
        return None

    refresh_token = data.get('refresh_token')
    client_id = data.get('client_id')
    scopes = data.get('scopes', '')

    if not refresh_token or not client_id:
        logger.error("Refresh token o client_id faltante en el archivo.")
        return None

    token_type = _detect_token_type(client_id)
    logger.info(f"Renovando access token (client: {client_id[:12]}..., tipo: {token_type['label']})")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Para tokens SPA de visualsupport, necesitamos redirect_uri y los scopes originales
            form_data = {
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": scopes or "openid profile email"
            }

            # Microsoft REQUIERE redirect_uri para clientes SPA
            if token_type['type'] == 'spa':
                form_data["redirect_uri"] = "https://visualsupport.microsoft.com"

            logger.info(f"Enviando refresh con scopes: '{form_data['scope']}', redirect_uri: {form_data.get('redirect_uri', 'N/A')}")

            response = await client.post(
                "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                data=form_data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://visualsupport.microsoft.com",
                    "Referer": "https://visualsupport.microsoft.com/",
                    "Accept": "application/json"
                }
            )

            if response.status_code == 200:
                token_data = response.json()
                new_access_token = token_data.get("access_token")
                new_refresh_token = token_data.get("refresh_token")
                expires_in = token_data.get("expires_in", 3600)

                if new_access_token:
                    # Guardar el nuevo access token
                    with open(TOKEN_CACHE_FILE, 'w') as f:
                        json.dump({
                            'token': new_access_token,
                            'expires_at': time.time() + expires_in - 120  # 2 min antes para seguridad
                        }, f)
                    logger.info(f"Nuevo access token obtenido. Expira en {expires_in // 60} minutos.")

                    # Actualizar el refresh token (Microsoft da uno nuevo cada vez)
                    if new_refresh_token:
                        save_refresh_token(new_refresh_token, client_id, scopes)
                        logger.info("Refresh token renovado automaticamente.")

                    return new_access_token
                else:
                    logger.error(f"Respuesta sin access_token: {token_data}")
                    return None
            else:
                error_data = response.json() if response.headers.get('content-type', '').startswith('application/json') else {}
                error_msg = error_data.get('error_description', response.text[:200])
                error_code = error_data.get('error', 'unknown')
                logger.error(f"Error renovando token (HTTP {response.status_code}): {error_msg}")

                # Si el refresh token expiró, alertar
                if response.status_code == 400 and ("expired" in str(error_msg).lower() or "AADSTS70000" in str(error_msg)):
                    logger.error("🔴 REFRESH TOKEN EXPIRADO. Se necesita re-autenticación.")
                    # Alerta Telegram
                    try:
                        from telegram_alert import send_alert
                        import asyncio
                        await send_alert(
                            f"🔴 *REFRESH TOKEN EXPIRADO*\n\n"
                            f"Error: `{error_code}`\n"
                            f"Tipo: {token_type['label']}\n\n"
                            f"Acciones:\n"
                            f"• `/deviceauth` — Device Code Flow (90 días)\n"
                            f"• `/setrefreshtoken` — Token manual"
                        )
                    except:
                        pass

                return None

    except Exception as e:
        logger.error(f"Error en HTTP request de refresh: {e}")
        return None


def save_refresh_token(refresh_token: str, client_id: str, scopes: str = ""):
    """Guarda el refresh token en disco con metadata."""
    token_type = _detect_token_type(client_id)

    with open(REFRESH_TOKEN_FILE, 'w') as f:
        json.dump({
            'refresh_token': refresh_token,
            'client_id': client_id,
            'scopes': scopes,
            'token_type': token_type['type'],
            'token_type_label': token_type['label'],
            'max_lifetime_days': token_type['max_lifetime_days'],
            'saved_at': time.time(),
            'saved_at_readable': time.strftime('%Y-%m-%d %H:%M:%S'),
            'last_refreshed_at': time.time()
        }, f, indent=2)


def get_refresh_token_status() -> dict:
    """Retorna el estado del refresh token con información REAL del tipo."""
    if not os.path.exists(REFRESH_TOKEN_FILE):
        return {"status": "no_token", "message": "No hay refresh token guardado."}

    try:
        with open(REFRESH_TOKEN_FILE, 'r') as f:
            data = json.load(f)

        saved_at = data.get('saved_at', 0)
        client_id = data.get('client_id', '')
        token_type = _detect_token_type(client_id)

        age_hours = (time.time() - saved_at) / 3600
        age_days = age_hours / 24
        max_days = token_type['max_lifetime_days']
        remaining_days = max_days - age_days

        # Para tokens SPA, la vida real se extiende con cada refresh proactivo
        last_refreshed = data.get('last_refreshed_at', saved_at)
        hours_since_refresh = (time.time() - last_refreshed) / 3600

        if token_type['type'] == 'spa':
            # SPA: el reloj se reinicia con cada refresh
            # Si el proactive refresher está activo, el token se mantiene vivo
            effective_remaining_hours = max(0, 24 - hours_since_refresh)

            if effective_remaining_hours > 0:
                return {
                    "status": "valid",
                    "token_type": token_type['type'],
                    "token_type_label": token_type['label'],
                    "age_hours": round(age_hours, 1),
                    "remaining_hours": round(effective_remaining_hours, 1),
                    "hours_since_last_refresh": round(hours_since_refresh, 1),
                    "client_id_prefix": client_id[:12],
                    "message": f"Token SPA activo. {round(effective_remaining_hours, 1)}h restantes (último refresh hace {round(hours_since_refresh, 1)}h).",
                    "warning": "Tokens SPA tienen límite de 24h. El proactive refresh lo mantiene activo."
                }
            else:
                return {
                    "status": "expired",
                    "token_type": token_type['type'],
                    "token_type_label": token_type['label'],
                    "message": "Token SPA expirado (>24h sin refresh). Renovar con /setrefreshtoken o /deviceauth."
                }
        else:
            # Native App: vida de 90 días sliding window
            if remaining_days > 0:
                return {
                    "status": "valid",
                    "token_type": token_type['type'],
                    "token_type_label": token_type['label'],
                    "age_days": int(age_days),
                    "remaining_days": int(remaining_days),
                    "hours_since_last_refresh": round(hours_since_refresh, 1),
                    "client_id_prefix": client_id[:12],
                    "message": f"Token {token_type['label']} válido. {int(remaining_days)} días restantes."
                }
            else:
                return {
                    "status": "expired",
                    "token_type": token_type['type'],
                    "token_type_label": token_type['label'],
                    "message": f"Token expirado (>{max_days} días). Regenerar con /deviceauth."
                }
    except Exception as e:
        return {"status": "error", "message": str(e)}
