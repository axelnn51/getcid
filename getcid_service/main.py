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
# EVENTO GLOBAL: se dispara cuando llega un token nuevo
# Permite que peticiones de CID en espera reintentan inmediatamente
# ============================================================
_token_renewed_event = asyncio.Event()

# ============================================================
# LOCK GLOBAL DE RENOVACIÓN
# Previene que múltiples componentes (boot, proactive, cron, API)
# lancen Playwright simultáneamente
# ============================================================
renovation_lock = asyncio.Lock()
_last_renovation_start = 0  # Timestamp de la última renovación iniciada
RENOVATION_COOLDOWN = 300  # 5 minutos mínimo entre renovaciones
MAX_RENOVATION_ATTEMPTS = 5  # Máximo de intentos antes de parar

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

    # ── BOOT VALIDATOR: valida el token REALMENTE contra Microsoft al arrancar ──
    async def startup_validator():
        """
        Se ejecuta al boot. NO confía en el expires_at del archivo — lo valida
        realmente contra Microsoft para garantizar que el primer CID funcione.

        Flujo:
        1. Token en caché con >5 min → prueba DPoP contra MS
           ✅ MS acepta → listo, notifica
           ❌ MS rechaza → va al paso 2
        2. Refresh token disponible → obtiene nuevo access token → valida
           ✅ válido → listo, notifica
           ❌ falla → paso 3
        3. Lanza Playwright (renovación completa)
        """
        _logger = logging.getLogger("BootValidator")
        await asyncio.sleep(8)  # Dar tiempo a uvicorn para estar 100% listo

        _logger.info(f"🔍 [{_peru_time_str()}] Boot Validator: iniciando validación real del token...")

        async def _validate_token_against_ms(token: str) -> bool:
            """
            Valida el token contra Microsoft con un IID dummy.
            Retorna True si el token es aceptado (cualquier respuesta que no sea 401/403).
            """
            try:
                result = await process_iid(
                    "000000000000000000000000000000000000000000000000000000",
                    token
                )
                error = result.get("error", "")
                # 401/403 = token rechazado. Cualquier otro error = token válido (IID inválido, etc.)
                if "Token expirado" in error or "Error: 401" in error or "Error: 403" in error:
                    _logger.warning(f"❌ [{_peru_time_str()}] Token rechazado por Microsoft (401/403)")
                    return False
                _logger.info(f"✅ [{_peru_time_str()}] Token validado por Microsoft (respuesta: {error[:60] if error else 'OK'})")
                return True
            except Exception as e:
                _logger.warning(f"⚠️ [{_peru_time_str()}] Error en validación contra MS: {e}")
                return False

        token_validated = False
        token_source = "ninguno"

        # ── PASO 1: Verificar token en caché ──
        try:
            import json as _json
            import time as _time
            if os.path.exists(TOKEN_CACHE_FILE):
                with open(TOKEN_CACHE_FILE, 'r') as _f:
                    _td = _json.load(_f)
                cached_token = _td.get('token')
                remaining = _td.get('expires_at', 0) - _time.time()

                if cached_token and remaining > 300:  # >5 min en el archivo
                    _logger.info(
                        f"🔍 [{_peru_time_str()}] Token en caché ({int(remaining // 60)} min según archivo). "
                        f"Validando contra Microsoft..."
                    )
                    token_validated = await _validate_token_against_ms(cached_token)
                    if token_validated:
                        token_source = f"caché ({int(remaining // 60)} min restantes)"
                    else:
                        _logger.warning(f"⚠️ [{_peru_time_str()}] Token en caché RECHAZADO por MS. Intentando refresh token...")
                else:
                    _logger.info(f"⏰ [{_peru_time_str()}] Token en caché expirado o ausente. Saltando al refresh token.")
        except Exception as e:
            _logger.warning(f"⚠️ [{_peru_time_str()}] Error leyendo caché: {e}")

        # ── PASO 2: Refresh token (si el caché falló) ──
        if not token_validated:
            try:
                from token_refresher import refresh_access_token, get_refresh_token_status
                rs = get_refresh_token_status()
                if rs.get("status") == "valid":
                    _logger.info(f"🔄 [{_peru_time_str()}] Intentando refresh token contra Microsoft...")
                    new_token = await refresh_access_token()
                    if new_token:
                        _logger.info(f"🔍 [{_peru_time_str()}] Nuevo access token obtenido. Validando...")
                        token_validated = await _validate_token_against_ms(new_token)
                        if token_validated:
                            token_source = "refresh token (sin CAPTCHA)"
                        else:
                            _logger.error(f"❌ [{_peru_time_str()}] Nuevo token también rechazado por MS.")
                    else:
                        _logger.warning(f"⚠️ [{_peru_time_str()}] Refresh token no pudo obtener access token.")
                else:
                    _logger.warning(f"⚠️ [{_peru_time_str()}] Refresh token no disponible ({rs.get('status', 'unknown')}).")
            except Exception as e:
                _logger.warning(f"⚠️ [{_peru_time_str()}] Error en refresh de boot: {e}")

        # ── RESULTADO ──
        if token_validated:
            _logger.info(f"🎉 [{_peru_time_str()}] Boot: sistema listo. Token válido vía {token_source}.")
            try:
                from telegram_alert import send_alert
                await send_alert(
                    f"🔄 *Servidor Reiniciado*\n\n"
                    f"⏰ {_peru_time_str()}\n"
                    f"✅ Token validado contra Microsoft\n"
                    f"📦 Fuente: {token_source}\n\n"
                    f"✅ *Sistema listo desde el primer CID.*"
                )
            except Exception:
                pass
        else:
            # ── PASO 3: Playwright (último recurso) ──
            _logger.warning(f"🚨 [{_peru_time_str()}] Boot: todo falló → lanzando Playwright...")
            try:
                from telegram_alert import send_alert
                await send_alert(
                    f"🔄 *Servidor Reiniciado*\n\n"
                    f"⏰ {_peru_time_str()}\n"
                    f"❌ Token inválido + refresh token fallido\n\n"
                    f"🚨 *Lanzando renovación Playwright automáticamente...*\n"
                    f"⏳ El sistema estará listo en ~2 minutos."
                )
            except Exception:
                pass
            try:
                import httpx as _httpx
                async with _httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post("http://localhost:8000/api/start-renovation")
                    _logger.info(f"🚀 [{_peru_time_str()}] Playwright lanzado en boot (status {resp.status_code}).")
            except Exception as e:
                _logger.error(f"❌ [{_peru_time_str()}] Error lanzando Playwright en boot: {e}")

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
    """Inicia el script de Playwright en background para renovar el token. Con límite de intentos."""
    global renovation_task, _last_renovation_start
    import logging
    logger = logging.getLogger("API")
    
    if renovation_task and not renovation_task.done():
        return JSONResponse(status_code=400, content={"success": False, "error": "Ya hay una renovación en progreso."})
    
    # Cooldown: no permitir renovaciones demasiado frecuentes
    now = time.time()
    elapsed_since_last = now - _last_renovation_start
    if _last_renovation_start > 0 and elapsed_since_last < RENOVATION_COOLDOWN:
        remaining = int(RENOVATION_COOLDOWN - elapsed_since_last)
        logger.info(f"⏸ [{_peru_time_str()}] Renovación en cooldown. Faltan {remaining}s.")
        return JSONResponse(status_code=429, content={"success": False, "error": f"Cooldown activo. Reintenta en {remaining}s."})
    
    _last_renovation_start = now
    
    async def limited_renovation():
        import subprocess
        import sys
        backoff_seconds = [120, 180, 300, 600, 900]  # 2m, 3m, 5m, 10m, 15m
        
        for attempt in range(1, MAX_RENOVATION_ATTEMPTS + 1):
            logger.info(f"🔄 [{_peru_time_str()}] Obtención automática (Intento {attempt}/{MAX_RENOVATION_ATTEMPTS})...")
            try:
                async with renovation_lock:
                    # Ejecutar en un proceso separado para aislar Playwright y su propio event loop,
                    # previniendo el error "Target page, context or browser has been closed" causado 
                    # por conflictos de event loop (uvloop vs asyncio) o limpieza de hilos en FastAPI.
                    process = await asyncio.create_subprocess_exec(
                        sys.executable, "remote_renovar.py",
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(os.path.abspath(__file__))
                    )
                    
                    while True:
                        line = await process.stdout.readline()
                        if not line:
                            break
                        # Opcional: imprimir logs del subprocess si se desea
                        logger.info(f"[Playwright] {line.decode('utf-8', errors='ignore').strip()}")
                        
                    await process.wait()
                    success = process.returncode == 0
                    
                if success:
                    logger.info(f"✅ [{_peru_time_str()}] Obtención exitosa en el intento {attempt}.")
                    # IMPORTANTE: El Access Token obtenido por Playwright está ligado a la llave DPoP del navegador.
                    # Debemos usar el Refresh Token inmediatamente para obtener un NUEVO Access Token
                    # ligado a la llave DPoP de este servidor (core.py).
                    try:
                        from token_refresher import refresh_access_token
                        logger.info("🔄 Forzando refresh token para vincular DPoP a la llave del servidor...")
                        await refresh_access_token()
                        logger.info("✅ DPoP vinculado exitosamente.")
                    except Exception as e:
                        logger.error(f"⚠️ Error forzando refresh token DPoP: {e}")
                    return
            except Exception as e:
                logger.error(f"❌ [{_peru_time_str()}] Error en obtención: {e}")
            
            if attempt < MAX_RENOVATION_ATTEMPTS:
                wait_time = backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)]
                logger.warning(f"⚠️ [{_peru_time_str()}] Intento {attempt} fallido. Reintentando en {wait_time//60}m...")
                await asyncio.sleep(wait_time)
        
        # Todos los intentos fallaron
        logger.error(f"🚨 [{_peru_time_str()}] {MAX_RENOVATION_ATTEMPTS} intentos agotados. Deteniendo.")
        try:
            from telegram_alert import send_alert
            await send_alert(
                f"🚨 *Renovación Fallida ({MAX_RENOVATION_ATTEMPTS} intentos)*\n\n"
                f"⏰ {_peru_time_str()}\n"
                f"Todos los intentos de obtener token fallaron.\n"
                f"Usa `/settoken` manual o `/deviceauth` para recuperar el sistema."
            )
        except: pass

    renovation_task = asyncio.create_task(limited_renovation())
    logger.info(f"🚀 [{_peru_time_str()}] Renovación con límite de {MAX_RENOVATION_ATTEMPTS} intentos iniciada.")
    return {"success": True, "message": f"Proceso de renovación iniciado (máx {MAX_RENOVATION_ATTEMPTS} intentos)."}

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
    """
    Endpoint de la API para procesar el IID.

    Sistema de resiliencia de 3 niveles cuando el token expira:
      Nivel 1 (rápido, ~3s): Refresh token sin CAPTCHA → reintenta CID → devuelve CID sin que el usuario note nada.
      Nivel 2 (espera, hasta 38s): Playwright corriendo en background → espera el nuevo token → reintenta CID → devuelve CID.
      Nivel 3 (error): Si todo lo anterior falla → avisa al usuario que espere 2-3 min.
    """
    global _last_token_expired_alert, _token_renewed_event
    import logging as _logging
    _log = _logging.getLogger("API")
    try:
        import traceback

        def _is_token_expired(res: dict) -> bool:
            err = res.get("error", "")
            return "Token expirado" in err or "Error: 401" in err or "Error: 403" in err

        # ── Intento 1: token actual ──
        result = await process_iid(req.iid)

        if result.get("success"):
            return JSONResponse(status_code=200, content=result)

        if not _is_token_expired(result):
            # Error que no es de token (checksum, bloqueado, etc.) → devolver directo
            return JSONResponse(status_code=400, content=result)

        # ═══ TOKEN EXPIRADO — sistema de auto-recuperación ═══
        _log.warning(f"🔴 [{_peru_time_str()}] Token expirado. Nivel 1: intentando refresh rápido...")

        # ── Nivel 1: Refresh token (sin CAPTCHA, ~2-3 segundos) ──
        try:
            from token_refresher import refresh_access_token
            new_token = await refresh_access_token()
            if new_token:
                _log.info(f"🔄 [{_peru_time_str()}] Nuevo token obtenido. Reintentando CID...")
                result = await process_iid(req.iid, new_token)
                if result.get("success"):
                    _log.info(f"✅ [{_peru_time_str()}] CID obtenido tras refresh automático (transparente al usuario).")
                    return JSONResponse(status_code=200, content=result)
                if _is_token_expired(result):
                    _log.warning(f"⚠️ [{_peru_time_str()}] Nuevo token también rechazado por MS. Nivel 2...")
                else:
                    # Error de IID, no de token
                    return JSONResponse(status_code=400, content=result)
        except Exception as re:
            _log.error(f"❌ [{_peru_time_str()}] Error en refresh rápido: {re}")

        # ── Nivel 2: Playwright en background + esperar token nuevo ──
        _log.warning(f"🚀 [{_peru_time_str()}] Nivel 2: lanzando Playwright y esperando token nuevo (máx 60s)...")
        await start_renovation()

        # Esperar a que /api/settoken dispare _token_renewed_event
        token_acquired = False
        try:
            _token_renewed_event.clear()
            await asyncio.wait_for(_token_renewed_event.wait(), timeout=60)  # 60s (antes 38s)
            _log.info(f"🎉 [{_peru_time_str()}] Token nuevo recibido durante espera. Reintentando CID...")
            token_acquired = True
        except asyncio.TimeoutError:
            _log.warning(f"⏱ [{_peru_time_str()}] Timeout de 60s. Verificando si el token llegó de todas formas...")
            # A veces el token llega justo después del timeout — verificar caché una vez más
            try:
                import json as _j
                if os.path.exists(TOKEN_CACHE_FILE):
                    with open(TOKEN_CACHE_FILE, 'r') as _f:
                        _td = _j.load(_f)
                    if _td.get('expires_at', 0) - time.time() > 300:  # Token con >5min restantes
                        token_acquired = True
                        _log.info(f"✅ [{_peru_time_str()}] Token encontrado en caché post-timeout!")
            except Exception:
                pass

        if token_acquired:
            # Leer el token nuevo del caché e intentar CID
            try:
                import json as _j
                with open(TOKEN_CACHE_FILE, 'r') as _f:
                    _td = _j.load(_f)
                fresh_token = _td.get('token')
                if fresh_token:
                    result = await process_iid(req.iid, fresh_token)
                    if result.get("success"):
                        _log.info(f"✅ [{_peru_time_str()}] CID obtenido tras espera de Playwright (transparente al usuario).")
                        return JSONResponse(status_code=200, content=result)
            except Exception as te:
                _log.error(f"❌ [{_peru_time_str()}] Error leyendo token tras espera: {te}")

        # ── Nivel 3: error — el usuario debe esperar ──
        _log.error(f"🚨 [{_peru_time_str()}] Todos los niveles fallaron. Devolviendo MS_TOKEN_RENEWING al usuario.")

        now = time.time()
        if now - _last_token_expired_alert > _TOKEN_ALERT_COOLDOWN:
            _last_token_expired_alert = now
            try:
                from telegram_alert import send_alert
                await send_alert(
                    f"🔴 *Token Expirado — CID Fallido*\n\n"
                    f"⏰ {_peru_time_str()}\n"
                    f"El token expiró y el refresh rápido + Playwright tardaron más de 60s.\n"
                    f"El sistema sigue trabajando en background."
                )
            except:
                pass

        return JSONResponse(status_code=400, content={
            "success": False,
            "error": "🔄 El sistema está renovando credenciales automáticamente. Reintenta en 1-2 minutos.",
            "code": "MS_TOKEN_RENEWING"
        })

    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        return JSONResponse(status_code=500, content={"success": False, "error": f"CRITICAL PYTHON ERROR: {str(e)}\n{error_trace}"})

@app.post("/api/settoken")
async def set_token(req: TokenRequest):
    """Recibe un token de Microsoft generado localmente y lo guarda en caché."""
    global _token_renewed_event
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

        # ★ NOTIFICAR a peticiones de CID en espera que hay token nuevo
        _token_renewed_event.set()
        # Resetear para el siguiente ciclo (async safe: los waiters ya despertaron)
        asyncio.get_event_loop().call_later(0.1, _token_renewed_event.clear)

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

    # Zombie processes (Chrome leftover detection)
    try:
        import subprocess
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)
        zombie_count = result.stdout.count('<defunct>')
        chrome_count = result.stdout.lower().count('chrome')
        status["processes"] = {
            "zombie_count": zombie_count,
            "chrome_count": chrome_count,
            "warning": "Zombies detectados — reiniciar contenedor" if zombie_count > 5 else None
        }
    except:
        status["processes"] = {"zombie_count": "unknown"}

    return status


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
