"""
Cron de Renovación Programada — VERSIÓN ANTI-CAÍDAS
Sistema inteligente que renueva el token ANTES de que expire.

MEJORAS V2:
- Threshold reducido de 23h55m a 21h (3 horas de margen vs 5 minutos)
- Si la primera renovación falla, reintenta 3 veces con backoff
- Monitorea el estado del token después de la renovación
- Alerta por Telegram si el cron detecta problemas
"""
import asyncio
import datetime
import logging
import time
import os
import json
import httpx

logger = logging.getLogger("CronRenovar")

# Tiempo antes de las 24 horas para renovar: 21h = 3 horas de margen
# (Antes era 23h55m = solo 5 min de margen, causaba caídas si Playwright tardaba)
RENEWAL_THRESHOLD_SECONDS = 21 * 3600  # 21 horas

# Intentos de renovación si falla
MAX_CRON_RETRIES = 3
RETRY_DELAY_SECONDS = [300, 600, 900]  # 5min, 10min, 15min


def _peru_time_str(ts=None):
    """Devuelve la hora en formato legible, hora Perú (UTC-5)."""
    if ts:
        utc_now = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
    else:
        utc_now = datetime.datetime.now(datetime.timezone.utc)
    peru_tz = datetime.timezone(datetime.timedelta(hours=-5))
    peru_now = utc_now.astimezone(peru_tz)
    return peru_now.strftime('%Y-%m-%d %H:%M:%S PET')


def _get_persist_dir() -> str:
    return "/app/persist" if os.path.isdir("/app/persist") else "."


async def _trigger_renovation_with_retries():
    """Lanza la renovación con reintentos si falla."""
    for attempt in range(1, MAX_CRON_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post("http://localhost:8000/api/start-renovation")
                if resp.status_code == 200:
                    logger.info(f"🚀 [{_peru_time_str()}] Renovación lanzada exitosamente (intento {attempt}).")
                    return True
                elif resp.status_code == 400:
                    logger.info(f"ℹ️ [{_peru_time_str()}] Ya hay renovación en progreso (400). OK.")
                    return True  # Ya está renovando, no necesitamos reintentar
                elif resp.status_code == 429:
                    logger.info(f"⏸ [{_peru_time_str()}] Renovación en cooldown (429). Esperando...")
                else:
                    logger.error(f"❌ [{_peru_time_str()}] Error HTTP {resp.status_code}: {resp.text[:100]}")
        except Exception as e:
            logger.error(f"❌ [{_peru_time_str()}] Error de red (intento {attempt}/{MAX_CRON_RETRIES}): {e}")
        
        if attempt < MAX_CRON_RETRIES:
            delay = RETRY_DELAY_SECONDS[min(attempt - 1, len(RETRY_DELAY_SECONDS) - 1)]
            logger.warning(f"⚠️ [{_peru_time_str()}] Reintentando en {delay // 60} minutos...")
            await asyncio.sleep(delay)
    
    # Todos los intentos fallaron
    logger.error(f"🚨 [{_peru_time_str()}] Cron: {MAX_CRON_RETRIES} intentos de renovación fallaron.")
    try:
        from telegram_alert import send_alert
        await send_alert(
            f"🚨 *Cron de Renovación Fallido*\n\n"
            f"⏰ {_peru_time_str()}\n"
            f"Se intentó renovar {MAX_CRON_RETRIES} veces y todas fallaron.\n"
            f"Usa `/deviceauth` o `/settoken` para recuperar manualmente."
        )
    except Exception:
        pass
    return False


async def _verify_token_after_renovation():
    """Verifica que el token sea válido después de esperar a que Playwright termine."""
    await asyncio.sleep(180)  # Esperar 3 min para que Playwright termine
    
    try:
        token_file = os.path.join(_get_persist_dir(), "ms_token.json")
        if os.path.exists(token_file):
            with open(token_file, 'r') as f:
                data = json.load(f)
            remaining = data.get('expires_at', 0) - time.time()
            if remaining > 300:  # >5 min restantes
                logger.info(f"✅ [{_peru_time_str()}] Token verificado post-cron: {int(remaining // 60)} min restantes.")
                return True
            else:
                logger.warning(f"⚠️ [{_peru_time_str()}] Token post-cron tiene solo {int(remaining // 60)} min restantes.")
                return False
        else:
            logger.warning(f"⚠️ [{_peru_time_str()}] No hay archivo de token después de la renovación.")
            return False
    except Exception as e:
        logger.error(f"❌ [{_peru_time_str()}] Error verificando token post-cron: {e}")
        return False


async def start_daily_cron():
    """
    Sistema Programado Inteligente:
    Duerme tranquilamente sin gastar recursos y se despierta 3 horas
    antes de que el token de 24 horas expire para renovarlo.
    """
    logger.info(f"🕒 [{_peru_time_str()}] Cron Programado Iniciado (threshold: {RENEWAL_THRESHOLD_SECONDS // 3600}h).")
    
    # Esperar un poco en el inicio para no pisar otros procesos
    await asyncio.sleep(10)
    
    while True:
        try:
            token_file = os.path.join(_get_persist_dir(), "ms_token.json")
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
                    logger.warning(f"⚠️ [{_peru_time_str()}] El threshold de {RENEWAL_THRESHOLD_SECONDS // 3600}h ya pasó. Lanzando obtención ahora...")
                    sleep_time = 0
                else:
                    horas = int(sleep_time // 3600)
                    minutos = int((sleep_time % 3600) // 60)
                    logger.info(
                        f"⏳ [{_peru_time_str()}] Durmiendo hasta la próxima obtención: "
                        f"{_peru_time_str(next_run_time)} (en {horas}h {minutos}m)"
                    )

            # Dormir exactamente hasta la hora calculada (sin loops de chequeo)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            
            # ¡Es hora de renovar!
            logger.info(f"🕛 [{_peru_time_str()}] ¡Despertando! Lanzando obtención programada...")
            
            success = await _trigger_renovation_with_retries()
            
            if success:
                # Verificar que el token se renovó correctamente
                token_ok = await _verify_token_after_renovation()
                if not token_ok:
                    logger.warning(f"⚠️ [{_peru_time_str()}] Token post-renovación no es válido. Reintentando...")
                    await _trigger_renovation_with_retries()
            
            # Dormir un rato antes de recalcular el próximo ciclo
            # (para darle tiempo al sistema de estabilizarse)
            await asyncio.sleep(600)
            
        except Exception as e:
            logger.error(f"❌ [{_peru_time_str()}] Error en el cron: {e}")
            await asyncio.sleep(60)
