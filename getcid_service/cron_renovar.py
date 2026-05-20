import asyncio
import datetime
import logging

logger = logging.getLogger("CronRenovar")

async def start_daily_cron():
    """Ejecuta remote_renovar.py todos los días a medianoche."""
    logger.info("🕒 Cron de Renovación iniciado. Esperando a la medianoche...")
    
    # Esperar un minuto en el inicio para no pisar otros procesos de arranque
    await asyncio.sleep(60)
    
    while True:
        now = datetime.datetime.now()
        
        # Calcular el tiempo hasta la próxima medianoche
        tomorrow = now + datetime.timedelta(days=1)
        next_midnight = datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0)
        
        seconds_until_midnight = (next_midnight - now).total_seconds()
        horas = int(seconds_until_midnight // 3600)
        minutos = int((seconds_until_midnight % 3600) // 60)
        
        logger.info(f"⏳ Faltan {horas} horas y {minutos} minutos para la próxima renovación automática.")
        
        # Dormir hasta la medianoche
        await asyncio.sleep(seconds_until_midnight)
        
        logger.info("🕛 ¡Es medianoche! Iniciando proceso automático de renovación (remote_renovar.py)...")
        try:
            from remote_renovar import run as run_renovar
            # IMPORTANTE: run_renovar ya es async y maneja sus propios errores
            await run_renovar()
            logger.info("✅ Renovación de medianoche finalizada.")
        except Exception as e:
            logger.error(f"❌ Error crítico en la renovación de medianoche: {e}")
            
        # Dormir un minuto extra para asegurar que ya pasó la medianoche y no se repita
        await asyncio.sleep(60)
