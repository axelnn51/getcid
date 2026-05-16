"""
Capa 2: Device Code Flow para tokens de 90 días.

El client_id actual (SPA de visualsupport) tiene un límite duro de 24h.
El Device Code Flow usa client_ids de tipo "native app" que otorgan
refresh tokens de 90 días.

Flujo:
1. Servidor genera un código (ej: "ABC-123") y lo envía por Telegram
2. Admin visita https://microsoft.com/devicelogin y pega el código
3. Inicia sesión con su cuenta Microsoft
4. Servidor captura el token automáticamente → 90 días de vida

NOTA: No está garantizado que un client_id nativo pueda generar tokens
válidos para la API de visualsupport.microsoft.com. Este módulo prueba
múltiples client_ids y reporta el resultado.
"""
import asyncio
import httpx
import json
import time
import os
import logging

logger = logging.getLogger("DeviceAuth")

# Directorio persistente
PERSIST_DIR = "/app/persist" if os.path.isdir("/app/persist") else "."
DEVICE_AUTH_LOG = os.path.join(PERSIST_DIR, "device_auth_log.json")

# Client IDs nativos a probar (en orden de probabilidad de funcionar)
NATIVE_CLIENT_IDS = [
    {
        "id": "04b07795-8ddb-461a-bbee-02f9e1bf7b46",
        "name": "Azure CLI",
        "scopes": "https://visualsupport.microsoft.com/.default offline_access"
    },
    {
        "id": "d3590ed6-52b3-4102-aeff-aad2292ab01c",
        "name": "Microsoft Office",
        "scopes": "https://visualsupport.microsoft.com/.default offline_access"
    },
    {
        "id": "04b07795-8ddb-461a-bbee-02f9e1bf7b46",
        "name": "Azure CLI (generic)",
        "scopes": "openid profile offline_access"
    },
]

# Estado global del flujo activo
_active_flow = None


async def start_device_code_flow(client_index: int = 0) -> dict:
    """
    Inicia el Device Code Flow con el client_id en el índice dado.
    Si falla, intenta automáticamente con el siguiente.
    """
    global _active_flow

    if client_index >= len(NATIVE_CLIENT_IDS):
        return {
            "success": False,
            "error": "Todos los client_ids fueron rechazados. El Device Code Flow no es compatible con esta API."
        }

    config = NATIVE_CLIENT_IDS[client_index]
    client_id = config["id"]
    scopes = config["scopes"]

    logger.info(f"Iniciando Device Code Flow con {config['name']} (client: {client_id[:12]}...)")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode",
                data={
                    "client_id": client_id,
                    "scope": scopes
                }
            )

            if resp.status_code != 200:
                error_data = resp.json() if "json" in resp.headers.get("content-type", "") else {}
                error_msg = error_data.get("error_description", f"HTTP {resp.status_code}")
                logger.warning(f"{config['name']} rechazado: {error_msg}")

                # Intentar siguiente client_id
                return await start_device_code_flow(client_index + 1)

            data = resp.json()

            _active_flow = {
                "device_code": data["device_code"],
                "user_code": data["user_code"],
                "verification_uri": data.get("verification_uri", "https://microsoft.com/devicelogin"),
                "client_id": client_id,
                "client_name": config["name"],
                "scopes": scopes,
                "expires_at": time.time() + data.get("expires_in", 900),
                "interval": data.get("interval", 5),
                "status": "pending",
                "client_index": client_index,
                "started_at": time.time()
            }

            user_code = data["user_code"]
            verification_uri = _active_flow["verification_uri"]
            logger.info(f"✅ Código generado: {user_code} | URI: {verification_uri}")

            # Enviar código al admin por Telegram
            await _notify_device_code(user_code, verification_uri, config["name"])

            # Iniciar polling automático en background
            asyncio.create_task(_auto_poll())

            return {
                "success": True,
                "user_code": user_code,
                "verification_uri": verification_uri,
                "client_name": config["name"],
                "expires_in": data.get("expires_in", 900),
                "message": f"Ingresa el código {user_code} en {verification_uri}"
            }

    except Exception as e:
        logger.error(f"Error iniciando Device Code Flow: {e}")
        return {"success": False, "error": str(e)}


async def _notify_device_code(user_code: str, verification_uri: str, client_name: str):
    """Notifica al admin por Telegram con el código del dispositivo."""
    try:
        from telegram_alert import send_alert
        await send_alert(
            f"🔐 *Device Code Flow Iniciado*\n\n"
            f"📋 Código: `{user_code}`\n"
            f"🌐 URL: {verification_uri}\n"
            f"🔧 Client: {client_name}\n\n"
            f"1️⃣ Abre el link\n"
            f"2️⃣ Pega el código\n"
            f"3️⃣ Inicia sesión con tu cuenta Microsoft\n\n"
            f"⏱ Expira en 15 minutos"
        )
    except Exception as e:
        logger.warning(f"No se pudo notificar código por Telegram: {e}")


async def _auto_poll():
    """Polling automático hasta que el usuario complete la autenticación."""
    global _active_flow

    if not _active_flow:
        return

    interval = _active_flow.get("interval", 5)
    max_wait = _active_flow.get("expires_at", time.time() + 900)

    logger.info(f"Iniciando auto-polling (intervalo: {interval}s)...")

    while time.time() < max_wait:
        if not _active_flow or _active_flow["status"] != "pending":
            return

        result = await _poll_once()

        if result.get("success"):
            logger.info("✅ Device Code Flow completado exitosamente!")

            # Notificar éxito
            try:
                from telegram_alert import send_alert
                await send_alert(
                    f"✅ *Device Auth Completado*\n\n"
                    f"🔧 Client: {_active_flow.get('client_name', '?')}\n"
                    f"🔑 Refresh token guardado\n"
                    f"📅 Válido por ~90 días\n\n"
                    f"El sistema ahora se auto-renovará automáticamente."
                )
            except:
                pass

            # Guardar log
            _save_auth_log(True, _active_flow.get("client_name", "?"))
            return

        status = result.get("status", "")
        if status == "pending":
            await asyncio.sleep(interval)
            continue

        # Error definitivo
        error = result.get("error", "Unknown")
        logger.error(f"Device Code Flow falló: {error}")

        # Si es error de scopes, intentar siguiente client_id
        if "AADSTS" in str(error) or "invalid_scope" in str(error).lower():
            next_index = _active_flow.get("client_index", 0) + 1
            if next_index < len(NATIVE_CLIENT_IDS):
                logger.info(f"Intentando siguiente client_id (índice {next_index})...")
                _active_flow = None
                await start_device_code_flow(next_index)
                return

        _save_auth_log(False, error)
        return

    # Timeout
    if _active_flow:
        _active_flow["status"] = "expired"
    logger.warning("Device Code Flow expiró (timeout)")


async def _poll_once() -> dict:
    """Hace un solo poll al endpoint de token."""
    global _active_flow

    if not _active_flow:
        return {"success": False, "error": "No active flow"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                data={
                    "client_id": _active_flow["client_id"],
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": _active_flow["device_code"]
                }
            )

            data = resp.json()

            if resp.status_code == 200 and "access_token" in data:
                # ¡ÉXITO!
                access_token = data["access_token"]
                refresh_token = data.get("refresh_token")
                expires_in = data.get("expires_in", 3600)

                # Guardar access token
                from token_refresher import TOKEN_CACHE_FILE, save_refresh_token
                with open(TOKEN_CACHE_FILE, "w") as f:
                    json.dump({
                        "token": access_token,
                        "expires_at": time.time() + expires_in - 120
                    }, f)

                # Guardar refresh token
                if refresh_token:
                    save_refresh_token(
                        refresh_token=refresh_token,
                        client_id=_active_flow["client_id"],
                        scopes=_active_flow["scopes"]
                    )

                _active_flow["status"] = "completed"

                return {
                    "success": True,
                    "has_refresh_token": bool(refresh_token),
                    "client_name": _active_flow["client_name"],
                    "token_type": "native_app",
                    "expected_lifetime_days": 90
                }

            error = data.get("error", "")
            error_desc = data.get("error_description", "")

            if "authorization_pending" in error:
                return {"success": False, "status": "pending"}

            if "authorization_declined" in error:
                _active_flow["status"] = "declined"
                return {"success": False, "error": "El usuario rechazó la autorización."}

            if "expired_token" in error:
                _active_flow["status"] = "expired"
                return {"success": False, "error": "El código expiró."}

            # Otros errores
            _active_flow["status"] = "error"
            return {"success": False, "status": "error", "error": error_desc or error}

    except Exception as e:
        logger.error(f"Error en poll: {e}")
        return {"success": False, "error": str(e)}


def _save_auth_log(success: bool, detail: str):
    """Guarda un log del intento de Device Auth."""
    log_entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "success": success,
        "detail": detail,
        "client_name": _active_flow.get("client_name", "?") if _active_flow else "?"
    }

    try:
        logs = []
        if os.path.exists(DEVICE_AUTH_LOG):
            with open(DEVICE_AUTH_LOG, "r") as f:
                logs = json.load(f)

        logs.append(log_entry)
        # Mantener solo últimos 50 entries
        logs = logs[-50:]

        with open(DEVICE_AUTH_LOG, "w") as f:
            json.dump(logs, f, indent=2)
    except Exception as e:
        logger.warning(f"Error guardando device auth log: {e}")


def get_device_auth_status() -> dict:
    """Retorna el estado actual del flujo de Device Auth."""
    if not _active_flow:
        return {"status": "inactive", "message": "No hay flujo activo. Usa /deviceauth para iniciar."}

    remaining = max(0, int(_active_flow["expires_at"] - time.time()))

    return {
        "status": _active_flow["status"],
        "user_code": _active_flow.get("user_code"),
        "verification_uri": _active_flow.get("verification_uri"),
        "client_name": _active_flow.get("client_name"),
        "remaining_seconds": remaining,
    }
