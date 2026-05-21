import asyncio
import datetime
import logging

logger = logging.getLogger("CronRenovar")

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 300  # 5 minutos entre reintentos
# Horas del día (UTC) a las que se renueva automáticamente
# Medianoche (00:00) + Mediodía (12:00) como backup
RENOVATION_HOURS = [0, 12]


async def _run_with_retries():
    """Ejecuta la renovación con reintentos y validación."""
    for attempt in range(1, MAX_RETRIES + 1):
        logger.info(f"🔄 Intento de renovación {attempt}/{MAX_RETRIES}...")
        try:
            from remote_renovar import run as run_renovar
            await run_renovar()
            
            # Verificar que el token sea válido después de renovar
            import os, json, time
            token_file = "ms_token.json"
            if os.path.exists(token_file):
                with open(token_file, 'r') as f:
                    data = json.load(f)
                if data.get('expires_at', 0) > time.time():
                    logger.info(f"✅ Renovación exitosa en intento {attempt}. Token válido.")
                    return True
                else:
                    logger.warning(f"⚠️ Intento {attempt}: Token guardado pero ya expirado.")
            else:
                logger.warning(f"⚠️ Intento {attempt}: No se generó archivo de token.")
                
        except Exception as e:
            logger.error(f"❌ Intento {attempt} falló: {e}")
        
        if attempt < MAX_RETRIES:
            logger.info(f"⏳ Esperando {RETRY_DELAY_SECONDS // 60} minutos antes del siguiente intento...")
            await asyncio.sleep(RETRY_DELAY_SECONDS)
    
    logger.error(f"❌ Todos los {MAX_RETRIES} intentos de renovación fallaron.")
    
    # Notificar fallo crítico a Telegram
    try:
        from telegram_alert import send_alert
        await send_alert(
            "🔴 *CRON: Renovación Automática Fallida*\n\n"
            f"Los {MAX_RETRIES} intentos de renovación fallaron.\n"
            "Se necesita intervención manual.\n\n"
            "• Usa 🔄 Renovar Token\n"
            "• O /deviceauth para re-autenticarse"
        )
    except:
        pass
    
    return False


async def start_daily_cron():
    """Ejecuta remote_renovar.py en horarios fijos del día."""
    logger.info(f"🕒 Cron de Renovación iniciado. Horarios: {RENOVATION_HOURS}h")
    
    # Esperar un minuto en el inicio para no pisar otros procesos de arranque
    await asyncio.sleep(60)
    
    while True:
        now = datetime.datetime.now()
        
        # Encontrar la próxima hora de renovación
        next_run = None
        for hour in sorted(RENOVATION_HOURS):
            candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate > now:
                next_run = candidate
                break
        
        # Si no hay más horas hoy, usar la primera hora de mañana
        if next_run is None:
            tomorrow = now + datetime.timedelta(days=1)
            next_run = tomorrow.replace(hour=sorted(RENOVATION_HOURS)[0], minute=0, second=0, microsecond=0)
        
        seconds_until = (next_run - now).total_seconds()
        horas = int(seconds_until // 3600)
        minutos = int((seconds_until % 3600) // 60)
        
        logger.info(f"⏳ Próxima renovación a las {next_run.strftime('%H:%M')} (en {horas}h {minutos}min)")
        
        # Dormir hasta la hora programada
        await asyncio.sleep(seconds_until)
        
        logger.info(f"🕛 ¡Hora de renovación! ({datetime.datetime.now().strftime('%H:%M')}) Iniciando proceso automático...")
        
        success = await _run_with_retries()
        
        if success:
            logger.info("✅ Renovación programada completada exitosamente.")
        else:
            logger.error("❌ Renovación programada falló después de todos los reintentos.")
            
        # Dormir un minuto extra para asegurar que ya pasó la hora y no se repita
        await asyncio.sleep(60)
