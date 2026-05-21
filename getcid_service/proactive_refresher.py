"""
Capa 1: Proactive Token Refresher
Background task que renueva el token cada 25 minutos, 24/7.
Evita que el token expire silenciosamente entre requests de clientes.

- Con client_id SPA (24h): mantiene el token "caliente" renovando antes de expirar
- Con client_id nativo (90d): renueva igualmente por seguridad
- Envía alertas Telegram si falla 2+ veces consecutivas
- AUTO-ESCALA: si falla 5+ veces, dispara remote_renovar automáticamente
"""
import asyncio
import time
import logging

logger = logging.getLogger("ProactiveRefresher")

REFRESH_INTERVAL_SECONDS = 25 * 60  # 25 minutos
INITIAL_DELAY_SECONDS = 60  # Esperar 1 min después del arranque
AUTO_RENOVATE_THRESHOLD = 5  # Fallos consecutivos antes de auto-renovar
AUTO_RENOVATE_COOLDOWN = 7200  # 2 horas entre auto-renovaciones

# Estado global
_running = False
_last_refresh_time = None
_last_refresh_ok = None
_consecutive_failures = 0
_total_refreshes = 0
_total_failures = 0
_last_auto_renovate_time = 0


async def start_proactive_refresh():
    """
    Background task infinito que renueva el token proactivamente.
    Debe ser lanzado como asyncio.create_task() en el startup de FastAPI.
    """
    global _running, _last_refresh_time, _last_refresh_ok
    global _consecutive_failures, _total_refreshes, _total_failures

    _running = True
    logger.info(f"🔄 Proactive Token Refresher INICIADO. Intervalo: {REFRESH_INTERVAL_SECONDS // 60} min")

    # Esperar un poco para que el servidor arranque completamente
    await asyncio.sleep(INITIAL_DELAY_SECONDS)

    while _running:
        try:
            from token_refresher import get_refresh_token_status, refresh_access_token

            # Verificar si hay refresh token configurado
            status = get_refresh_token_status()

            if status.get("status") == "no_token":
                logger.info("⏸ Sin refresh token configurado. Esperando...")
                await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
                continue

            # Intentar renovar
            logger.info("🔄 Proactive refresh iniciando...")
            new_token = await refresh_access_token()

            if new_token:
                _last_refresh_time = time.time()
                _last_refresh_ok = True
                _consecutive_failures = 0
                _total_refreshes += 1
                logger.info(f"✅ Proactive refresh OK #{_total_refreshes}. Próximo en {REFRESH_INTERVAL_SECONDS // 60} min.")
            else:
                _last_refresh_time = time.time()
                _last_refresh_ok = False
                _consecutive_failures += 1
                _total_failures += 1
                logger.error(f"❌ Proactive refresh FALLÓ (consecutivos: {_consecutive_failures})")

                # Alerta Telegram después de 2 fallos consecutivos
                if _consecutive_failures == 2:
                    await _send_failure_alert()

                # AUTO-ESCALACIÓN: disparar remote_renovar después de N fallos
                if _consecutive_failures >= AUTO_RENOVATE_THRESHOLD:
                    await _auto_renovate()

        except Exception as e:
            _consecutive_failures += 1
            _total_failures += 1
            logger.error(f"💥 Error en proactive refresh: {e}")

            if _consecutive_failures == 2:
                await _send_failure_alert()
            
            if _consecutive_failures >= AUTO_RENOVATE_THRESHOLD:
                await _auto_renovate()

        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


async def _auto_renovate():
    """Auto-escala disparando remote_renovar cuando el refresh token falla mucho."""
    global _last_auto_renovate_time

    now = time.time()
    if now - _last_auto_renovate_time < AUTO_RENOVATE_COOLDOWN:
        elapsed_min = int((now - _last_auto_renovate_time) / 60)
        cooldown_min = AUTO_RENOVATE_COOLDOWN // 60
        logger.info(f"⏸ Auto-renovación en cooldown ({elapsed_min}/{cooldown_min} min). Esperando...")
        return

    _last_auto_renovate_time = now
    logger.info("🚀 AUTO-ESCALACIÓN: Disparando remote_renovar automáticamente...")

    try:
        from telegram_alert import send_alert
        await send_alert(
            "🤖 *Auto-Renovación Activada*\n\n"
            f"Fallos consecutivos: *{_consecutive_failures}*\n"
            "Disparando renovación automática por Playwright..."
        )
    except:
        pass

    try:
        from remote_renovar import run as run_renovar
        await run_renovar()
        logger.info("✅ Auto-renovación completada.")
    except Exception as e:
        logger.error(f"❌ Auto-renovación falló: {e}")
        try:
            from telegram_alert import send_alert
            await send_alert(
                f"❌ *Auto-Renovación Falló*\n\n"
                f"Error: `{str(e)[:200]}`\n\n"
                "Se necesita intervención manual.\n"
                "Usa 🔄 Renovar Token o /deviceauth"
            )
        except:
            pass


async def _send_failure_alert():
    """Envía alerta por Telegram cuando el refresh falla múltiples veces."""
    try:
        from telegram_alert import send_alert
        await send_alert(
            f"🔴 *ALERTA: Token Refresh Fallido*\n\n"
            f"Fallos consecutivos: *{_consecutive_failures}*\n"
            f"El access token podría expirar pronto.\n\n"
            f"Acciones posibles:\n"
            f"• `/setrefreshtoken` — Renovar refresh token\n"
            f"• `/deviceauth` — Iniciar Device Code Flow\n"
            f"• `/tokenstatus` — Ver estado actual"
        )
    except Exception as e:
        logger.error(f"Error enviando alerta Telegram: {e}")


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
        "auto_renovate_threshold": AUTO_RENOVATE_THRESHOLD,
        "last_auto_renovate": _last_auto_renovate_time or None
    }


def stop_proactive_refresh():
    """Detiene el proactive refresher."""
    global _running
    _running = False
    logger.info("⏹ Proactive Token Refresher DETENIDO.")
