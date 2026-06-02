import asyncio
import datetime
import logging
import time
import os
import json
import httpx

logger = logging.getLogger("ContinuousWatcher")

# Tiempo antes de las 24 horas para renovar (23h 55m = 24h - 5m)
RENEWAL_THRESHOLD_SECONDS = (24 * 3600) - (5 * 60)
WATCHER_INTERVAL_SECONDS = 60  # Revisar cada minuto

def _peru_time_str():
    """Devuelve la hora actual en formato legible, hora Perú (UTC-5)."""
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    peru_tz = datetime.timezone(datetime.timedelta(hours=-5))
    peru_now = utc_now.astimezone(peru_tz)
    return peru_now.strftime('%Y-%m-%d %H:%M:%S PET')

async def start_daily_cron():
    """
    Vigila continuamente el estado del token.
    Asegura que la obtención se ejecute 5 minutos antes de que expiren las 24 horas
    de la sesión de Microsoft.
    """
    logger.info(f"🕒 [{_peru_time_str()}] Watcher Continuo Iniciado. "
                f"Renovará automáticamente cada {RENEWAL_THRESHOLD_SECONDS / 3600:.2f} horas.")
    
    # Esperar un poco en el inicio para no pisar otros procesos
    await asyncio.sleep(60)
    
    while True:
        try:
            token_file = "ms_token.json"
            needs_renovation = False
            reason = ""
            
            if os.path.exists(token_file):
                with open(token_file, 'r') as f:
                    data = json.load(f)
                    
                last_run = data.get('last_playwright_run', 0)
                expires_at = data.get('expires_at', 0)
                now = time.time()
                
                # 1. Chequear si pasaron 23h 55m desde la última ejecución de Playwright
                if last_run > 0 and (now - last_run) >= RENEWAL_THRESHOLD_SECONDS:
                    needs_renovation = True
                    reason = "Han pasado 23h 55m desde la última obtención de Playwright."
                
                # 2. Chequear si el token cacheado expiró (como capa de seguridad extra)
                elif expires_at > 0 and expires_at < now:
                    needs_renovation = True
                    reason = "El token en memoria ha expirado completamente."
            else:
                needs_renovation = True
                reason = "No existe archivo de token."

            if needs_renovation:
                logger.warning(f"⚠️ [{_peru_time_str()}] Renovación requerida: {reason}")
                
                # Disparar renovación en la API
                async with httpx.AsyncClient(timeout=10) as client:
                    try:
                        resp = await client.post("http://localhost:8000/api/start-renovation")
                        if resp.status_code == 200:
                            logger.info(f"🚀 [{_peru_time_str()}] Ciclo infinito de obtención activado correctamente.")
                        else:
                            logger.error(f"❌ [{_peru_time_str()}] Error activando ciclo: {resp.text}")
                    except Exception as e:
                        logger.error(f"❌ [{_peru_time_str()}] Error de red activando ciclo: {e}")
                
                # Esperar 15 minutos para que la obtención tenga tiempo de terminar 
                # y no estar disparando la API constantemente
                await asyncio.sleep(900)
                continue
                
        except Exception as e:
            logger.error(f"❌ [{_peru_time_str()}] Error en el watcher: {e}")
            
        # Dormir 1 minuto y volver a chequear
        await asyncio.sleep(WATCHER_INTERVAL_SECONDS)
