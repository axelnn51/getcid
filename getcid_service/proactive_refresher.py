"""
Capa 1: Proactive Token Refresher — VERSIÓN ANTI-CAÍDAS
Background task que renueva el access token usando el refresh token cada 25 minutos, 24/7.
Evita que el token expire silenciosamente entre requests de clientes.

MEJORAS V2:
- Detecta cuándo el refresh token SPA está por expirar (>20h) y lanza Playwright preventivo
- Reduce el intervalo de 30min a 25min para mayor margen de seguridad
- Verifica que el access token realmente funcione (no solo que exista)
- Envía alertas escalonadas por Telegram con información útil
"""
import asyncio
import datetime
import time
import logging
import os
import json

logger = logging.getLogger("ProactiveRefresher")

REFRESH_INTERVAL_SECONDS = 25 * 60  # 25 minutos (antes era 30, más margen de seguridad)
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


def _get_persist_dir() -> str:
    return "/app/persist" if os.path.isdir("/app/persist") else "."


def _check_spa_token_approaching_death() -> dict:
    """Verifica si el refresh token SPA está próximo a expirar (>20h desde último Playwright).
    Retorna dict con info del estado."""
    try:
        token_file = os.path.join(_get_persist_dir(), "ms_token.json")
        if not os.path.exists(token_file):
            return {"needs_playwright": False, "reason": "no_token_file"}
        
        with open(token_file, 'r') as f:
            data = json.load(f)
        
        last_playwright = data.get('last_playwright_run', 0)
        if last_playwright == 0:
            return {"needs_playwright": False, "reason": "no_playwright_record"}
        
        hours_since_playwright = (time.time() - last_playwright) / 3600
        
        # El token SPA tiene hard limit de 24h. Si han pasado >20h, necesitamos renovar
        # ANTES de que expire, no DESPUÉS (que es lo que causaba las caídas)
        if hours_since_playwright >= 20:
            return {
                "needs_playwright": True,
                "hours_since_playwright": round(hours_since_playwright, 1),
                "reason": f"SPA token con {round(hours_since_playwright, 1)}h — se acerca al límite de 24h"
            }
        
        return {
            "needs_playwright": False,
            "hours_since_playwright": round(hours_since_playwright, 1),
            "reason": f"OK — {round(hours_since_playwright, 1)}h desde último Playwright"
        }
    except Exception as e:
        return {"needs_playwright": False, "reason": f"error: {e}"}


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

            # ─── CHECK PREVENTIVO: ¿El token SPA está por morir? ───
            spa_check = _check_spa_token_approaching_death()
            if spa_check.get("needs_playwright"):
                hours = spa_check.get("hours_since_playwright", "?")
                logger.warning(
                    f"🚨 [{_peru_time_str()}] Token SPA con {hours}h de antigüedad — "
                    f"lanzando renovación PREVENTIVA (antes de que expire a las 24h)..."
                )
                try:
                    from telegram_alert import send_alert
                    await send_alert(
                        f"🔄 *Renovación Preventiva*\n\n"
                        f"⏰ {_peru_time_str()}\n"
                        f"El token SPA tiene *{hours}h* de antigüedad.\n"
                        f"Renovando ANTES de que expire a las 24h.\n\n"
                        f"✅ Esto evita caídas del servicio."
                    )
                except Exception:
                    pass
                await _trigger_renovation()

            # Intentar renovar access token via refresh token
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
        spa_check = _check_spa_token_approaching_death()
        spa_info = f"\n📊 SPA: {spa_check.get('reason', 'N/A')}" if spa_check else ""
        
        await send_alert(
            f"🔴 *ALERTA: Token Refresh Fallido*\n\n"
            f"⏰ {_peru_time_str()}\n"
            f"Fallos consecutivos: *{_consecutive_failures}*\n"
            f"Total fallos: *{_total_failures}*{spa_info}\n\n"
            f"El access token podría expirar pronto.\n"
            f"🤖 *Lanzando renovación automática via Playwright...*"
        )
        logger.info(f"📱 [{_peru_time_str()}] Alerta enviada a Telegram.")
    except Exception as e:
        logger.error(f"Error enviando alerta Telegram: {e}")


_last_renovation_trigger = 0
RENOVATION_COOLDOWN = 1800  # 30 min entre Playwright triggers (antes era 1h — más reactivo)

async def _trigger_renovation():
    """Lanza el ciclo de renovación Playwright automáticamente, respetando el lock global."""
    global _last_renovation_trigger

    now = time.time()
    if now - _last_renovation_trigger < RENOVATION_COOLDOWN:
        remaining = int(RENOVATION_COOLDOWN - (now - _last_renovation_trigger))
        logger.info(f"⏸ [{_peru_time_str()}] Renovación Playwright en cooldown ({remaining}s restantes). No se lanza.")
        return

    # Verificar si ya hay una renovación en progreso (desde main.py)
    try:
        from main import renovation_task, renovation_lock
        if renovation_task and not renovation_task.done():
            logger.info(f"⏸ [{_peru_time_str()}] Ya hay renovación en progreso (main.renovation_task). No se lanza otra.")
            return
        if renovation_lock.locked():
            logger.info(f"⏸ [{_peru_time_str()}] renovation_lock está ocupado. No se lanza otra.")
            return
    except ImportError:
        pass

    _last_renovation_trigger = now
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=10) as client:
            resp = await client.post("http://localhost:8000/api/start-renovation")
            if resp.status_code == 200:
                logger.info(f"🚀 [{_peru_time_str()}] Renovación Playwright lanzada (status {resp.status_code}).")
            elif resp.status_code == 400:
                logger.info(f"ℹ️ [{_peru_time_str()}] Ya hay renovación en progreso (400). OK.")
            elif resp.status_code == 429:
                logger.info(f"⏸ [{_peru_time_str()}] Renovación en cooldown del servidor (429).")
            else:
                logger.error(f"❌ [{_peru_time_str()}] Error lanzando renovación: {resp.text}")
    except Exception as e:
        logger.error(f"❌ [{_peru_time_str()}] No se pudo lanzar renovación automática: {e}")


def get_refresher_status() -> dict:
    """Retorna el estado del proactive refresher."""
    spa_check = _check_spa_token_approaching_death()
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
        "spa_token_status": spa_check.get("reason", "N/A"),
        "spa_needs_playwright": spa_check.get("needs_playwright", False),
    }


def stop_proactive_refresh():
    """Detiene el proactive refresher."""
    global _running
    _running = False
    logger.info(f"⏹ [{_peru_time_str()}] Proactive Token Refresher DETENIDO.")
