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

TOKEN_CACHE_FILE = "ms_token.json"

# ============================================================
# ESTADO GLOBAL PARA CAPTCHA REMOTO
# ============================================================
captcha_event = asyncio.Event()
captcha_clicks = 0
renovation_task = None


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
        logger.info("✅ Proactive Token Refresher lanzado en background")
    except Exception as e:
        logger.error(f"⚠️ No se pudo iniciar Proactive Refresher: {e}")
        refresher_task = None

    # Lanzar Cron Diario de Renovación a la medianoche
    try:
        from cron_renovar import start_daily_cron
        cron_task = asyncio.create_task(start_daily_cron())
        logger.info("✅ Cron Diario (Medianoche) lanzado en background")
    except Exception as e:
        logger.error(f"⚠️ No se pudo iniciar Cron Diario: {e}")
        cron_task = None

    yield  # Servidor corriendo

    # Shutdown: detener refresher
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


# ============================================================
# ENDPOINTS EXISTENTES
# ============================================================

@app.post("/api/start-renovation")
async def start_renovation():
    """Inicia el script de Playwright en background para renovar el token."""
    global renovation_task
    import logging
    logger = logging.getLogger("API")
    
    if renovation_task and not renovation_task.done():
        return JSONResponse(status_code=400, content={"success": False, "error": "Ya hay una renovación en progreso."})
    
    try:
        from remote_renovar import run as run_renovar
        renovation_task = asyncio.create_task(run_renovar())
        logger.info("🚀 Tarea remota de renovación de token iniciada.")
        return {"success": True, "message": "Proceso de renovación iniciado."}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

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
    try:
        import traceback
        result = await process_iid(req.iid)
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
        expires_at = time.time() + req.duration
        with open(TOKEN_CACHE_FILE, 'w') as f:
            json.dump({
                'token': req.token,
                'expires_at': expires_at
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
