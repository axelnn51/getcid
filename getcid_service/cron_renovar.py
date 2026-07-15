import asyncio
import datetime
import logging
import time
import os
import json
import httpx

logger = logging.getLogger("CronRenovar")

# Tiempo antes de las 24 horas para renovar (23h 55m = 24h - 5m)
RENEWAL_THRESHOLD_SECONDS = (24 * 3600) - (5 * 60)

def _peru_time_str(ts=None):
    """Devuelve la hora en formato legible, hora Perú (UTC-5)."""
    if ts:
        utc_now = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
    else:
        utc_now = datetime.datetime.now(datetime.timezone.utc)
    peru_tz = datetime.timezone(datetime.timedelta(hours=-5))
    peru_now = utc_now.astimezone(peru_tz)
    return peru_now.strftime('%Y-%m-%d %H:%M:%S PET')

async def start_daily_cron():
    """
    Sistema Programado Inteligente:
    Duerme tranquilamente sin gastar recursos y se despierta exactamente
    5 minutos antes de que el token de 24 horas expire para renovarlo.
    """
    logger.info(f"🕒 [{_peru_time_str()}] Cron Programado Iniciado.")
    
    # Esperar un poco en el inicio para no pisar otros procesos
    await asyncio.sleep(10)
    
    while True:
        try:
            # Usar el mismo directorio persistente que token_refresher.py
            # En Docker: /app/persist/ms_token.json (sobrevive reinicios del contenedor)
            # En local: ./ms_token.json
            import os as _os
            _persist = "/app/persist" if _os.path.isdir("/app/persist") else "."
            token_file = _os.path.join(_persist, "ms_token.json")
            last_run = 0
            
            if os.path.exists(token_file):
                try:
                    with open(token_file, 'r') as f:
                        data = json.load(f)
                    last_run = data.get('last_playwright_run', 0)
                except Exception:
                    pass

            
            now = time.time()
            
            if last_run == 0:
                logger.warning(f"⚠️ [{_peru_time_str()}] No hay registro previo del token. Lanzando obtención ahora...")
                sleep_time = 0
            else:
                next_run_time = last_run + RENEWAL_THRESHOLD_SECONDS
                sleep_time = next_run_time - now
                
                if sleep_time <= 0:
                    logger.warning(f"⚠️ [{_peru_time_str()}] El tiempo de 23h 55m ya pasó. Lanzando obtención ahora...")
                    sleep_time = 0
                else:
                    horas = int(sleep_time // 3600)
                    minutos = int((sleep_time % 3600) // 60)
                    logger.info(f"⏳ [{_peru_time_str()}] Durmiendo hasta la próxima obtención programada: "
                                f"{_peru_time_str(next_run_time)} (en {horas}h {minutos}m)")

            # Dormir exactamente hasta la hora calculada (sin loops de chequeo)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            
            # ¡Es hora de renovar! (Lanzamos el sistema automático infinito)
            logger.info(f"🕛 [{_peru_time_str()}] ¡Despertando! Lanzando obtención automática 5 min antes de expirar...")
            
            async with httpx.AsyncClient(timeout=10) as client:
                try:
                    resp = await client.post("http://localhost:8000/api/start-renovation")
                    if resp.status_code == 200:
                        logger.info(f"🚀 [{_peru_time_str()}] Sistema infinito de obtención activado.")
                    else:
                        logger.error(f"❌ [{_peru_time_str()}] Error activando sistema: {resp.text}")
                except Exception as e:
                    logger.error(f"❌ [{_peru_time_str()}] Error de red activando sistema: {e}")
            
            # Dormir unos minutos para darle tiempo al sistema infinito de terminar
            # antes de volver a calcular el próximo ciclo de 24 horas.
            await asyncio.sleep(600)
            
        except Exception as e:
            logger.error(f"❌ [{_peru_time_str()}] Error en el cron: {e}")
            await asyncio.sleep(60)
