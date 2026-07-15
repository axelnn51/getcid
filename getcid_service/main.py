from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
import json
import time
import os
import asyncio
from contextlib import asynccontextmanager
from core import process_iid

_PERSIST_DIR = "/app/persist" if __import__("os").path.isdir("/app/persist") else "."
TOKEN_CACHE_FILE = __import__("os").path.join(_PERSIST_DIR, "ms_token.json")

# Cooldown para alertas de token expirado (evitar spam)
_last_token_expired_alert = 0
_TOKEN_ALERT_COOLDOWN = 1800  # 30 minutos

# ============================================================
# ESTADO GLOBAL PARA CAPTCHA REMOTO
# ============================================================
captcha_event = asyncio.Event()
captcha_clicks = 0
renovation_task = None


def _peru_time_str():
    """Devuelve la hora actual en formato legible, hora Perú (UTC-5)."""
    import datetime as _dt
    utc_now = _dt.datetime.now(_dt.timezone.utc)
    peru_tz = _dt.timezone(_dt.timedelta(hours=-5))
    peru_now = utc_now.astimezone(peru_tz)
    return peru_now.strftime('%Y-%m-%d %H:%M:%S PET')


# ============================================================
# STARTUP / SHUTDOWN — Lanzar proactive refresher al arrancar
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager: inicia el proactive refresher al arrancar."""
    import logging
    logger = logging.getLogger("Startup")

    # Lanzar proactive refresher en background
    try:
        from proactive_refresher import start_proactive_refresh
        refresher_task = asyncio.create_task(start_proactive_refresh())
        logger.info(f"✅ [{_peru_time_str()}] Proactive Token Refresher lanzado en background")
    except Exception as e:
        logger.error(f"⚠️ [{_peru_time_str()}] No se pudo iniciar Proactive Refresher: {e}")
        refresher_task = None

    # Lanzar Cron Diario de Renovación (medianoche Perú = 05:00 UTC)
    try:
        from cron_renovar import start_daily_cron
        cron_task = asyncio.create_task(start_daily_cron())
        logger.info(f"✅ [{_peru_time_str()}] Cron Diario (Medianoche Perú) lanzado en background")
    except Exception as e:
        logger.error(f"⚠️ [{_peru_time_str()}] No se pudo iniciar Cron Diario: {e}")
        cron_task = None

    # ── BOOT VALIDATOR: verifica token al arrancar y actúa inmediatamente ──
    async def startup_validator():
        """
        Se ejecuta 15 segundos después del boot.
        1. Notifica en Telegram que el servidor arrancó.
        2. Verifica si el token es válido, está por expirar, o ya expiró.
        3. Si expiró o le quedan < 30 min → lanza renovación inmediata.
        """
        await asyncio.sleep(15)  # Esperar a que uvicorn esté 100% listo
        _logger = logging.getLogger("BootValidator")
        _logger.info(f"🔍 [{_peru_time_str()}] Boot Validator iniciado. Verificando estado del sistema...")

        # Leer estado del token
        token_status = "unknown"
        remaining_min = 0
        try:
            if os.path.exists(TOKEN_CACHE_FILE):
                with open(TOKEN_CACHE_FILE, 'r') as f:
                    data = json.load(f)
                remaining = data.get('expires_at', 0) - time.time()
                remaining_min = int(remaining // 60)
                if remaining > 0:
                    token_status = "valid"
                else:
                    token_status = "expired"
            else:
                token_status = "no_file"
        except Exception as e:
            token_status = f"error: {e}"

        # Leer estado del refresh token
        refresh_status = "unknown"
        try:
            from token_refresher import get_refresh_token_status
            rs = get_refresh_token_status()
            refresh_status = rs.get("status", "unknown")
        except Exception:
            pass

        # Construir mensaje de Telegram
        token_emoji = "✅" if token_status == "valid" else "❌"
        refresh_emoji = "✅" if refresh_status == "valid" else "⚠️"
        time_info = f"({remaining_min} min restantes)" if token_status == "valid" else ""

        boot_msg = (
            f"🔄 *Servidor Reiniciado*\n\n"
            f"⏰ {_peru_time_str()}\n"
            f"{token_emoji} Access Token: `{token_status}` {time_info}\n"
            f"{refresh_emoji} Refresh Token: `{refresh_status}`\n\n"
        )

        needs_renovation = False

        if token_status == "expired" or token_status == "no_file":
            boot_msg += "🚨 *Token expirado — lanzando renovación automática...*"
            needs_renovation = True
        elif token_status == "valid" and remaining_min < 30:
            boot_msg += f"⚠️ *Token con solo {remaining_min} min restantes — renovando preventivamente...*"
            needs_renovation = True
        else:
            boot_msg += f"✅ *Todo OK. El sistema reanudó operaciones normalmente.*"

        # Notificar a Telegram
        try:
            from telegram_alert import send_alert
            await send_alert(boot_msg)
            _logger.info(f"📱 [{_peru_time_str()}] Notificación de boot enviada a Telegram.")
        except Exception as e:
            _logger.warning(f"⚠️ No se pudo notificar boot por Telegram: {e}")

        # Si necesita renovar, lanzar ahora
        if needs_renovation:
            _logger.warning(f"🚀 [{_peru_time_str()}] Boot Validator: lanzando renovación de emergencia...")
            try:
                async with __import__('httpx').AsyncClient(timeout=10) as client:
                    resp = await client.post("http://localhost:8000/api/start-renovation")
                    _logger.info(f"🚀 [{_peru_time_str()}] Renovación lanzada (status {resp.status_code}).")
            except Exception as e:
                _logger.error(f"❌ [{_peru_time_str()}] Error lanzando renovación de boot: {e}")
        else:
            _logger.info(f"✅ [{_peru_time_str()}] Token válido ({remaining_min} min). No se necesita renovación.")

    boot_task = asyncio.create_task(startup_validator())

    yield  # Servidor corriendo

    # Shutdown: detener todo
    if refresher_task:
        try:
            from proactive_refresher import stop_proactive_refresh
            stop_proactive_refresh()
            refresher_task.cancel()
        except:
            pass
            
    if cron_task:
        try:
            cron_task.cancel()
        except:
            pass

    if boot_task and not boot_task.done():
        boot_task.cancel()


app = FastAPI(
    title="GetCID API Server",
    description="Servidor interno para obtener Confirmation IDs",
    lifespan=lifespan
)


class IIDRequest(BaseModel):
    iid: str

class TokenRequest(BaseModel):
    token: str
    duration: int = 3600  # Duración en segundos (default: 1 hora)
    is_playwright: bool = False


# ============================================================
# ENDPOINTS EXISTENTES
# ============================================================

@app.post("/api/start-renovation")
async def start_renovation():
    """Inicia el script de Playwright en background para renovar el token. Modo Infinito."""
    global renovation_task
    import logging
    logger = logging.getLogger("API")
    
    if renovation_task and not renovation_task.done():
        return JSONResponse(status_code=400, content={"success": False, "error": "Ya hay una renovación en progreso."})
    
    async def infinite_renovation():
        from remote_renovar import run as run_renovar
        attempt = 1
        while True:
            logger.info(f"🔄 [{_peru_time_str()}] Iniciando obtención automática (Intento {attempt})...")
            try:
                success = await run_renovar()
                if success:
                    logger.info(f"✅ [{_peru_time_str()}] Obtención exitosa en el intento {attempt}.")
                    break
            except Exception as e:
                logger.error(f"❌ [{_peru_time_str()}] Error de excepción en obtención: {e}")
            
            logger.warning(f"⚠️ [{_peru_time_str()}] Intento {attempt} fallido. Reintentando en 2 minutos para no ser bloqueados inmediatamente...")
            attempt += 1
            await asyncio.sleep(120)

    renovation_task = asyncio.create_task(infinite_renovation())
    logger.info(f"🚀 [{_peru_time_str()}] Ciclo infinito de obtención de token iniciado.")
    return {"success": True, "message": "Proceso de renovación infinito iniciado."}

@app.post("/api/solve-captcha")
async def solve_captcha(req: dict):
    """Recibe los clics del usuario desde Telegram y despierta a Playwright."""
    global captcha_event, captcha_clicks
    clicks = req.get("clicks", 0)
    captcha_clicks = clicks
    captcha_event.set()
    return {"success": True, "message": f"Se enviaron {clicks} clics."}

@app.post("/api/getcid")
async def api_getcid(req: IIDRequest):
    """Endpoint de la API para procesar el IID."""
    global _last_token_expired_alert
    try:
        import traceback
        result = await process_iid(req.iid)
        
        # Si el token expiró, disparar renovación infinita y avisar al cliente
        if not result.get("success") and "Token expirado" in result.get("error", ""):
            import logging
            logger = logging.getLogger("API")
            logger.warning(f"🔴 [{_peru_time_str()}] Token expirado detectado en petición de CID.")
            
            await start_renovation()
            
            # Alerta Telegram con cooldown de 30 min
            now = time.time()
            if now - _last_token_expired_alert > _TOKEN_ALERT_COOLDOWN:
                _last_token_expired_alert = now
                try:
                    from telegram_alert import send_alert
                    await send_alert(
                        f"🔴 *Token Expirado — CID Fallido*\n\n"
                        f"⏰ {_peru_time_str()}\n"
                        f"El token expiró al intentar activar.\n"
                        f"El sistema **ya activó el ciclo infinito** para obtener uno nuevo automáticamente."
                    )
                except:
                    pass
            
            return JSONResponse(status_code=400, content={
                "success": False, 
                "error": "🔄 El sistema detectó que el token expiró y ya está trabajando en ciclo infinito para obtener uno nuevo. Reintenta en unos minutos.",
                "code": "MS_TOKEN_RENEWING"
            })
            
        if result.get("success"):
            return JSONResponse(status_code=200, content=result)
        else:
            return JSONResponse(status_code=400, content=result)
    except Exception as e:
        error_trace = traceback.format_exc()
        return JSONResponse(status_code=500, content={"success": False, "error": f"CRITICAL PYTHON ERROR: {str(e)}\n{error_trace}"})

@app.post("/api/settoken")
async def set_token(req: TokenRequest):
    """Recibe un token de Microsoft generado localmente y lo guarda en caché."""
    try:
        import os
        last_run = 0
        if os.path.exists(TOKEN_CACHE_FILE):
            try:
                with open(TOKEN_CACHE_FILE, 'r') as f:
                    data = json.load(f)
                    last_run = data.get('last_playwright_run', 0)
            except: pass

        if req.is_playwright:
            last_run = time.time()

        expires_at = time.time() + req.duration
        with open(TOKEN_CACHE_FILE, 'w') as f:
            json.dump({
                'token': req.token,
                'expires_at': expires_at,
                'last_playwright_run': last_run
            }, f)
        
        minutes_left = req.duration // 60
        return JSONResponse(status_code=200, content={
            "success": True, 
            "message": f"Token guardado exitosamente. Válido por {minutes_left} minutos.",
            "expires_at": expires_at
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/api/token-status")
async def token_status():
    """Devuelve el estado actual del token cacheado + info del proactive refresher."""
    result = {}

    # Estado del access token
    if not os.path.exists(TOKEN_CACHE_FILE):
        result["access_token"] = {"status": "no_token", "message": "No hay token guardado."}
    else:
        try:
            with open(TOKEN_CACHE_FILE, 'r') as f:
                data = json.load(f)
            
            expires_at = data.get('expires_at', 0)
            remaining = expires_at - time.time()
            
            if remaining > 0:
                result["access_token"] = {
                    "status": "valid",
                    "remaining_seconds": int(remaining),
                    "remaining_minutes": int(remaining // 60),
                    "message": f"Token válido por {int(remaining // 60)} minutos más."
                }
                # Compat con formato antiguo
                result["status"] = "valid"
                result["remaining_minutes"] = int(remaining // 60)
            else:
                result["access_token"] = {
                    "status": "expired",
                    "expired_ago_seconds": int(abs(remaining)),
                    "message": f"Token expiró hace {int(abs(remaining) // 60)} minutos."
                }
                result["status"] = "expired"
        except Exception as e:
            result["access_token"] = {"status": "error", "message": str(e)}
            result["status"] = "error"

    # Estado del proactive refresher
    try:
        from proactive_refresher import get_refresher_status
        result["proactive_refresher"] = get_refresher_status()
    except:
        result["proactive_refresher"] = {"running": False}

    return result


class RefreshTokenRequest(BaseModel):
    refresh_token: str
    client_id: str
    scopes: str = ""

@app.post("/api/setrefreshtoken")
async def set_refresh_token(req: RefreshTokenRequest):
    """Recibe un refresh token de Microsoft para auto-renovación."""
    try:
        from token_refresher import save_refresh_token
        save_refresh_token(req.refresh_token, req.client_id, req.scopes)
        
        # Intentar obtener un access token inmediatamente
        from token_refresher import refresh_access_token
        new_token = await refresh_access_token()
        
        if new_token:
            return JSONResponse(status_code=200, content={
                "success": True,
                "message": "Refresh token guardado y access token generado. Renovación automática activa."
            })
        else:
            return JSONResponse(status_code=200, content={
                "success": True,
                "message": "Refresh token guardado, pero no se pudo generar access token inmediatamente. Se intentará en la próxima solicitud."
            })
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/api/refreshtoken-status")
async def refresh_token_status():
    """Estado detallado del refresh token con tipo real."""
    try:
        from token_refresher import get_refresh_token_status
        return get_refresh_token_status()
    except ImportError:
        return {"status": "error", "message": "Módulo token_refresher no disponible."}


# ============================================================
# NUEVOS ENDPOINTS — Device Code Flow (Capa 2)
# ============================================================

@app.post("/api/device-auth-start")
async def device_auth_start():
    """Inicia el Device Code Flow. Genera un código para que el admin lo ingrese en microsoft.com/devicelogin."""
    try:
        from device_auth import start_device_code_flow
        result = await start_device_code_flow()
        return JSONResponse(status_code=200 if result.get("success") else 400, content=result)
    except ImportError:
        return JSONResponse(status_code=500, content={"success": False, "error": "Módulo device_auth no disponible."})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/api/device-auth-status")
async def device_auth_status():
    """Estado del flujo de Device Code Flow activo."""
    try:
        from device_auth import get_device_auth_status
        return get_device_auth_status()
    except ImportError:
        return {"status": "error", "message": "Módulo device_auth no disponible."}


# ============================================================
# NUEVO ENDPOINT — Estado completo del sistema
# ============================================================

@app.get("/api/system-status")
async def system_status():
    """Estado completo de todos los componentes del sistema."""
    status = {
        "server": "ok",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Access token
    try:
        ts = await token_status()
        status["access_token"] = ts.get("access_token", ts)
    except:
        status["access_token"] = {"status": "error"}

    # Refresh token
    try:
        from token_refresher import get_refresh_token_status
        status["refresh_token"] = get_refresh_token_status()
    except:
        status["refresh_token"] = {"status": "error"}

    # Proactive refresher
    try:
        from proactive_refresher import get_refresher_status
        status["proactive_refresher"] = get_refresher_status()
    except:
        status["proactive_refresher"] = {"running": False}

    # Device auth
    try:
        from device_auth import get_device_auth_status
        status["device_auth"] = get_device_auth_status()
    except:
        status["device_auth"] = {"status": "not_available"}

    return status


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
