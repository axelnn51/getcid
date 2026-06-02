import asyncio
import datetime
import logging
import time

logger = logging.getLogger("CronRenovar")

MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 600  # 10 minutos entre reintentos (dar tiempo a que Microsoft desbloquee)

# Hora UTC a la que se renueva = 05:00 UTC = 00:00 hora Perú (UTC-5)
RENOVATION_HOUR_UTC = 5


def _peru_time_str():
    """Devuelve la hora actual en formato legible, hora Perú (UTC-5)."""
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    peru_tz = datetime.timezone(datetime.timedelta(hours=-5))
    peru_now = utc_now.astimezone(peru_tz)
    return peru_now.strftime('%Y-%m-%d %H:%M:%S PET')


async def _run_with_retries():
    """Ejecuta la renovación con reintentos y validación."""
    for attempt in range(1, MAX_RETRIES + 1):
        logger.info(f"🔄 [{_peru_time_str()}] Intento de renovación {attempt}/{MAX_RETRIES}...")
        try:
            from remote_renovar import run as run_renovar
            await run_renovar()
            
            # Verificar que el token sea válido después de renovar
            import os, json
            token_file = "ms_token.json"
            if os.path.exists(token_file):
                with open(token_file, 'r') as f:
                    data = json.load(f)
                if data.get('expires_at', 0) > time.time():
                    logger.info(f"✅ [{_peru_time_str()}] Renovación exitosa en intento {attempt}. Token válido.")
                    return True
                else:
                    logger.warning(f"⚠️ [{_peru_time_str()}] Intento {attempt}: Token guardado pero ya expirado.")
            else:
                logger.warning(f"⚠️ [{_peru_time_str()}] Intento {attempt}: No se generó archivo de token.")
                
        except Exception as e:
            error_str = str(e).lower()
            logger.error(f"❌ [{_peru_time_str()}] Intento {attempt} falló: {e}")
            
            # Si Microsoft bloqueó la cuenta, NO reintentar (sería peor)
            if "too many" in error_str or "blocked" in error_str or "locked" in error_str:
                logger.error(f"🚫 [{_peru_time_str()}] Cuenta bloqueada por Microsoft. Abortando reintentos.")
                break
        
        if attempt < MAX_RETRIES:
            logger.info(f"⏳ [{_peru_time_str()}] Esperando {RETRY_DELAY_SECONDS // 60} minutos antes del siguiente intento...")
            await asyncio.sleep(RETRY_DELAY_SECONDS)
    
    logger.error(f"❌ [{_peru_time_str()}] Renovación fallida después de todos los intentos.")
    
    # Notificar fallo crítico a Telegram
    try:
        from telegram_alert import send_alert
        await send_alert(
            "🔴 *CRON: Renovación Automática Fallida*\n\n"
            f"⏰ {_peru_time_str()}\n"
            f"Los {MAX_RETRIES} intentos de renovación fallaron.\n"
            "Se necesita intervención manual.\n\n"
            "• Usa 🔄 Renovar Token\n"
            "• O /deviceauth para re-autenticarse"
        )
    except:
        pass
    
    return False


async def start_daily_cron():
    """Ejecuta remote_renovar.py UNA VEZ al día a medianoche hora Perú (05:00 UTC)."""
    logger.info(f"🕒 [{_peru_time_str()}] Cron de Renovación iniciado. Horario: {RENOVATION_HOUR_UTC}:00 UTC (00:00 Perú)")
    
    # Esperar un minuto en el inicio para no pisar otros procesos de arranque
    await asyncio.sleep(60)
    
    while True:
        now = datetime.datetime.now(datetime.timezone.utc)
        
        # Calcular la próxima ejecución a las RENOVATION_HOUR_UTC:00 UTC
        next_run = now.replace(hour=RENOVATION_HOUR_UTC, minute=0, second=0, microsecond=0)
        if next_run <= now:
            # Ya pasó la hora hoy, programar para mañana
            next_run += datetime.timedelta(days=1)
        
        seconds_until = (next_run - now).total_seconds()
        horas = int(seconds_until // 3600)
        minutos = int((seconds_until % 3600) // 60)
        
        # Mostrar en hora Perú para fácil lectura
        peru_tz = datetime.timezone(datetime.timedelta(hours=-5))
        next_run_peru = next_run.astimezone(peru_tz)
        
        logger.info(
            f"⏳ [{_peru_time_str()}] Próxima renovación: "
            f"{next_run_peru.strftime('%Y-%m-%d %H:%M PET')} "
            f"({next_run.strftime('%H:%M UTC')}) — "
            f"en {horas}h {minutos}min"
        )
        
        # Dormir hasta la hora programada
        await asyncio.sleep(seconds_until)
        
        logger.info(f"🕛 [{_peru_time_str()}] ¡Hora de renovación! Iniciando proceso automático...")
        
        success = await _run_with_retries()
        
        if success:
            logger.info(f"✅ [{_peru_time_str()}] Renovación programada completada exitosamente.")
        else:
            logger.error(f"❌ [{_peru_time_str()}] Renovación programada falló.")
            
        # Dormir 2 minutos extra para asegurar que ya pasó la hora y no se repita
        await asyncio.sleep(120)
