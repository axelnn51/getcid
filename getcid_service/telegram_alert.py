"""
Módulo de alertas Telegram para el servicio Python.
Envía mensajes críticos al admin cuando hay problemas con tokens.
"""
import os
import logging
import httpx

logger = logging.getLogger("TelegramAlert")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [x.strip() for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


async def send_alert(message: str):
    """Envía un mensaje de alerta a todos los admins por Telegram."""
    if not BOT_TOKEN or not ADMIN_IDS:
        logger.warning("BOT_TOKEN o ADMIN_IDS no configurados. Alerta no enviada.")
        return False

    sent = False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for admin_id in ADMIN_IDS:
                try:
                    resp = await client.post(TELEGRAM_API, json={
                        "chat_id": admin_id,
                        "text": message,
                        "parse_mode": "Markdown"
                    })
                    if resp.status_code == 200:
                        sent = True
                    else:
                        logger.warning(f"Telegram API error para {admin_id}: {resp.status_code}")
                except Exception as e:
                    logger.warning(f"Error enviando alerta a {admin_id}: {e}")
    except Exception as e:
        logger.error(f"Error general en send_alert: {e}")

    return sent
