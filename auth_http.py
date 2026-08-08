import asyncio
import json
import httpx
import os
import logging
from logging.handlers import RotatingFileHandler
import random
from core import DPoPEngine

# Asegurar que el directorio de logs exista
os.makedirs("logs", exist_ok=True)

# Configurar logs con rotación (máx 5MB, hasta 3 archivos de respaldo)
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_handler = RotatingFileHandler("logs/backend.log", maxBytes=5*1024*1024, backupCount=3)
log_handler.setFormatter(log_formatter)

logger = logging.getLogger("AuthHTTP")
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)
# También enviar a consola
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)

TOKEN_URL = "https://login.live.com/oauth20_token.srf" # Or https://login.microsoftonline.com/common/oauth2/v2.0/token depending on the specific flow
CLIENT_ID = "29d9ed98-a469-4536-ade2-f981bc1d605e" # Ejemplo de cliente (o usar el de la app si tienes uno específico)
SESSION_FILE = "session_master.json"

class AuthManager:
    def __init__(self):
        self.dpop_engine = DPoPEngine()
        self.refresh_token = None
        self.access_token = None
        self.daemon_status = "inactive"
        self.daemon_error = None
        self._load_session()

    def _load_session(self):
        if not os.path.exists(SESSION_FILE):
            logger.warning("No se encontró session_master.json. Esperando a que el Extractor Local lo genere...")
            return

        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Intentar extraer del network captures si existe
            if "tokens_network" in data and "refresh_token" in data["tokens_network"]:
                self.refresh_token = data["tokens_network"]["refresh_token"]
                logger.info("Refresh token cargado desde tokens_network.")
            else:
                # Extraer desde storage_state (Cookies o LocalStorage)
                # Esta lógica dependerá de exactamente dónde Microsoft guarda el token.
                # Normalmente está en el sessionStorage o localStorage.
                logger.info("Analizando storage_state en busca de tokens...")
                # TODO: Implementar extracción específica desde cookies/localStorage si es necesario.
                # Por ahora asumimos que el script de extracción logra capturarlo en network.
                pass

    async def refresh_access_token(self):
        if not self.refresh_token:
            logger.error("No hay refresh_token disponible. No se puede renovar.")
            return False

        # Generar firma DPoP
        dpop_proof = self.dpop_engine.generate_dpop_proof("POST", TOKEN_URL)
        
        headers = {
            "DPoP": dpop_proof,
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        payload = {
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "token_type": "pop" # IMPORTANTE: Obligamos a que el token sea de prueba de posesión
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(TOKEN_URL, headers=headers, data=payload)
                data = response.json()
                
                if response.status_code == 200:
                    self.access_token = data.get("access_token")
                    # Actualizar el refresh token si nos dieron uno nuevo
                    if "refresh_token" in data:
                        self.refresh_token = data["refresh_token"]
                    logger.info("✅ Token renovado exitosamente (Silencioso).")
                    return True
                else:
                    logger.error(f"❌ Error al renovar token: {response.status_code} - {response.text}")
                    # Comprobar si el token está muerto permanentemente
                    if data.get("error") == "invalid_grant":
                        logger.error("🚨 CRÍTICO: El refresh_token ha sido revocado o expiró permanentemente.")
                        self.daemon_status = "failed"
                        self.daemon_error = "invalid_grant - Refresh token revocado"
                        self.refresh_token = None
                        self.access_token = None
                    return False
            except Exception as e:
                logger.error(f"❌ Excepción durante la petición HTTP: {str(e)}")
                return False

    async def start_daemon(self):
        logger.info("Demonio de renovación de token iniciado con Jitter.")
        self.daemon_status = "running"
        while True:
            if self.daemon_status == "failed":
                logger.warning("Demonio detenido por error crítico. Esperando nuevo session_master.json...")
                self._load_session()
                if self.refresh_token:
                    self.daemon_status = "running"
                    self.daemon_error = None
                    logger.info("Nuevo token detectado. Reanudando demonio.")
                else:
                    await asyncio.sleep(60) # Esperar un poco antes de volver a chequear si se subió el archivo
                    continue
            else:
                self._load_session() # Intentar recargar por si hay actualizaciones

            if self.refresh_token:
                await self.refresh_access_token()
            else:
                logger.info("Esperando refresh_token...")
            
            # Generar un Jitter (espera aleatoria entre 10 y 13 horas)
            jitter_seconds = random.randint(36000, 46800)
            logger.info(f"Próxima renovación en {jitter_seconds // 3600}h {(jitter_seconds % 3600) // 60}m.")
            await asyncio.sleep(jitter_seconds)

# Instancia global para ser usada por FastAPI
auth_manager = AuthManager()
