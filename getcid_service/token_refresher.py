"""
Renovador automático de tokens usando Microsoft Refresh Token.
100% gratis, sin CAPTCHA, sin navegador.

Flujo:
1. El usuario se loguea UNA VEZ localmente (cada 90 días)
2. Se captura el Refresh Token del navegador
3. El servidor usa el Refresh Token para generar Access Tokens nuevos cada hora
4. Cada refresh también renueva el Refresh Token → cadena infinita
"""
import httpx
import json
import time
import os
import logging

logger = logging.getLogger("TokenRefresher")

REFRESH_TOKEN_FILE = "ms_refresh_token.json"
TOKEN_CACHE_FILE = "ms_token.json"


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

    logger.info(f"Renovando access token con refresh token (client_id: {client_id[:12]}...)")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                data={
                    "client_id": client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "scope": scopes
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://visualsupport.microsoft.com"
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
                logger.error(f"Error renovando token (HTTP {response.status_code}): {error_msg}")

                # Si el refresh token expiró, informar al usuario
                if response.status_code == 400 and "expired" in str(error_msg).lower():
                    logger.error("REFRESH TOKEN EXPIRADO. El admin debe generar uno nuevo localmente.")

                return None

    except Exception as e:
        logger.error(f"Error en HTTP request de refresh: {e}")
        return None


def save_refresh_token(refresh_token: str, client_id: str, scopes: str = ""):
    """Guarda el refresh token en disco."""
    with open(REFRESH_TOKEN_FILE, 'w') as f:
        json.dump({
            'refresh_token': refresh_token,
            'client_id': client_id,
            'scopes': scopes,
            'saved_at': time.time(),
            'saved_at_readable': time.strftime('%Y-%m-%d %H:%M:%S')
        }, f, indent=2)


def get_refresh_token_status() -> dict:
    """Retorna el estado del refresh token."""
    if not os.path.exists(REFRESH_TOKEN_FILE):
        return {"status": "no_token", "message": "No hay refresh token guardado."}

    try:
        with open(REFRESH_TOKEN_FILE, 'r') as f:
            data = json.load(f)

        saved_at = data.get('saved_at', 0)
        age_days = (time.time() - saved_at) / 86400
        remaining_days = 90 - age_days

        if remaining_days > 0:
            return {
                "status": "valid",
                "age_days": int(age_days),
                "remaining_days": int(remaining_days),
                "message": f"Refresh token valido. {int(remaining_days)} dias restantes."
            }
        else:
            return {
                "status": "expired",
                "message": "Refresh token expirado (>90 dias). Regenerar localmente."
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}
