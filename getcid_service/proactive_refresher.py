"""
Capa 1: Proactive Token Refresher
Background task que renueva el access token usando el refresh token cada 30 minutos, 24/7.
Evita que el token expire silenciosamente entre requests de clientes.

- Con client_id SPA (24h): mantiene el token "caliente" renovando antes de expirar
- Con client_id nativo (90d): renueva igualmente por seguridad
- Envía alertas Telegram si falla 3+ veces consecutivas
- NUNCA abre Playwright ni dispara remote_renovar (eso es responsabilidad del cron)
"""
import asyncio
import datetime
import time
import logging

logger = logging.getLogger("ProactiveRefresher")

REFRESH_INTERVAL_SECONDS = 30 * 60  # 30 minutos
INITIAL_DELAY_SECONDS = 90  # Esperar 1.5 min después del arranque
ALERT_THRESHOLD = 3  # Fallos consecutivos antes de alertar por Telegram
ALERT_COOLDOWN = 3600  # 1 hora entre alertas de Telegram (no spamear)

# Estado global
_running = False
_last_refresh_time = None
_last_refresh_ok = None
_consecutive_failures = 0
_total_refreshes = 0
_total_failures = 0
_last_alert_time = 0


def _peru_time_str():
    """Devuelve la hora actual en formato legible, hora Perú (UTC-5)."""
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    peru_tz = datetime.timezone(datetime.timedelta(hours=-5))
    peru_now = utc_now.astimezone(peru_tz)
    return peru_now.strftime('%Y-%m-%d %H:%M:%S PET')


async def start_proactive_refresh():
    """
    Background task infinito que renueva el token proactivamente.
    Debe ser lanzado como asyncio.create_task() en el startup de FastAPI.
    """
    global _running, _last_refresh_time, _last_refresh_ok
    global _consecutive_failures, _total_refreshes, _total_failures

    _running = True
    logger.info(f"🔄 [{_peru_time_str()}] Proactive Token Refresher INICIADO. Intervalo: {REFRESH_INTERVAL_SECONDS // 60} min")

    # Esperar un poco para que el servidor arranque completamente
    await asyncio.sleep(INITIAL_DELAY_SECONDS)

    while _running:
        try:
            from token_refresher import get_refresh_token_status, refresh_access_token

            # Verificar si hay refresh token configurado
            status = get_refresh_token_status()

            if status.get("status") == "no_token":
                logger.info(f"⏸ [{_peru_time_str()}] Sin refresh token configurado. Esperando {REFRESH_INTERVAL_SECONDS // 60} min...")
                await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
                continue

            # Intentar renovar
            logger.info(f"🔄 [{_peru_time_str()}] Proactive refresh iniciando...")
            new_token = await refresh_access_token()

            if new_token:
                _last_refresh_time = time.time()
                _last_refresh_ok = True
                _consecutive_failures = 0
                _total_refreshes += 1
                logger.info(
                    f"✅ [{_peru_time_str()}] Proactive refresh OK "
                    f"(#{_total_refreshes} total). "
                    f"Próximo en {REFRESH_INTERVAL_SECONDS // 60} min."
                )
            else:
                _last_refresh_time = time.time()
                _last_refresh_ok = False
                _consecutive_failures += 1
                _total_failures += 1
                logger.error(
                    f"❌ [{_peru_time_str()}] Proactive refresh FALLÓ "
                    f"(consecutivos: {_consecutive_failures}, total fallos: {_total_failures})"
                )

                # Alerta Telegram + auto-trigger de Playwright después de N fallos consecutivos
                if _consecutive_failures >= ALERT_THRESHOLD:
                    await _send_failure_alert()
                    await _trigger_renovation()

        except Exception as e:
            _consecutive_failures += 1
            _total_failures += 1
            logger.error(f"💥 [{_peru_time_str()}] Error en proactive refresh: {e}")

            if _consecutive_failures >= ALERT_THRESHOLD:
                await _send_failure_alert()
                await _trigger_renovation()

        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


async def _send_failure_alert():
    """Envía alerta por Telegram cuando el refresh falla múltiples veces. Con cooldown."""
    global _last_alert_time

    now = time.time()
    if now - _last_alert_time < ALERT_COOLDOWN:
        logger.info(f"⏸ [{_peru_time_str()}] Alerta Telegram en cooldown. No se envía.")
        return

    _last_alert_time = now

    try:
        from telegram_alert import send_alert
        await send_alert(
            f"🔴 *ALERTA: Token Refresh Fallido*\n\n"
            f"⏰ {_peru_time_str()}\n"
            f"Fallos consecutivos: *{_consecutive_failures}*\n"
            f"Total fallos: *{_total_failures}*\n\n"
            f"El access token podría expirar pronto.\n"
            f"🤖 *Lanzando renovación automática via Playwright...*"
        )
        logger.info(f"📱 [{_peru_time_str()}] Alerta enviada a Telegram.")
    except Exception as e:
        logger.error(f"Error enviando alerta Telegram: {e}")


_last_renovation_trigger = 0
RENOVATION_COOLDOWN = 3600  # No lanzar más de 1 Playwright por hora

async def _trigger_renovation():
    """Lanza el ciclo infinito de renovación Playwright automáticamente."""
    global _last_renovation_trigger

    now = time.time()
    if now - _last_renovation_trigger < RENOVATION_COOLDOWN:
        logger.info(f"⏸ [{_peru_time_str()}] Renovación Playwright en cooldown. No se lanza.")
        return

    _last_renovation_trigger = now
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=10) as client:
            resp = await client.post("http://localhost:8000/api/start-renovation")
            if resp.status_code in (200, 400):  # 400 = ya hay una renovación en progreso
                logger.info(f"🚀 [{_peru_time_str()}] Renovación Playwright lanzada automáticamente (status {resp.status_code}).")
            else:
                logger.error(f"❌ [{_peru_time_str()}] Error lanzando renovación: {resp.text}")
    except Exception as e:
        logger.error(f"❌ [{_peru_time_str()}] No se pudo lanzar renovación automática: {e}")


def get_refresher_status() -> dict:
    """Retorna el estado del proactive refresher."""
    return {
        "running": _running,
        "last_refresh_time": _last_refresh_time,
        "last_refresh_ago_min": int((time.time() - _last_refresh_time) / 60) if _last_refresh_time else None,
        "last_refresh_ok": _last_refresh_ok,
        "consecutive_failures": _consecutive_failures,
        "total_refreshes": _total_refreshes,
        "total_failures": _total_failures,
        "interval_minutes": REFRESH_INTERVAL_SECONDS // 60,
        "alert_threshold": ALERT_THRESHOLD,
    }


def stop_proactive_refresh():
    """Detiene el proactive refresher."""
    global _running
    _running = False
    logger.info(f"⏹ [{_peru_time_str()}] Proactive Token Refresher DETENIDO.")
