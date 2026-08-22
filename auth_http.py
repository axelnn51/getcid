import asyncio
import json
import httpx
import os
import logging
from logging.handlers import RotatingFileHandler
import random
import base64
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

TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
# El CLIENT_ID se leerá dinámicamente del session_master.json
DEFAULT_CLIENT_ID = "29d9ed98-a469-4536-ade2-f981bc1d605e"
ORIGIN = "https://account.microsoft.com"
SESSION_FILE = "session_master.json"

class AuthManager:
    def __init__(self):
        self.dpop_engine = DPoPEngine()
        self.refresh_token = None
        self.access_token = None
        self.client_id = DEFAULT_CLIENT_ID
        self.daemon_status = "inactive"
        self.daemon_error = None
        self.puid = ""
        self._load_session()

    def _load_session(self):
        if not os.path.isfile(SESSION_FILE):
            logger.warning("No se encontró session_master.json (o está montado como directorio). Esperando a que el Extractor Local lo genere...")
            return

        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Error al leer session_master.json: {e}")
            return
            
        # Intentar extraer del network captures si existe
        if "tokens_network" in data and "refresh_token" in data["tokens_network"]:
            self.refresh_token = data["tokens_network"]["refresh_token"]
            self.access_token = data["tokens_network"].get("access_token")
            self.client_id = data["tokens_network"].get("client_id", DEFAULT_CLIENT_ID)
            
            pem_string = data.get("dpop_key")
            if pem_string:
                self.dpop_engine = DPoPEngine(pem_string)
            else:
                self.dpop_engine = DPoPEngine()
                
            logger.info(f"Tokens cargados desde tokens_network para el cliente: {self.client_id}")
        else:
            # Extraer desde storage_state (Cookies o LocalStorage)
            # Esta lógica dependerá de exactamente dónde Microsoft guarda el token.
            # Normalmente está en el sessionStorage o localStorage.
            logger.info("Analizando storage_state en busca de tokens...")
            # TODO: Implementar extracción específica desde cookies/localStorage si es necesario.
            # Por ahora asumimos que el script de extracción logra capturarlo en network.
            pass

    def _save_session(self):
        """Guardar tokens actualizados de forma ATÓMICA para resistir cortes de luz."""
        tmp_file = SESSION_FILE + ".tmp"
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            if "tokens_network" not in data:
                data["tokens_network"] = {}
                
            data["tokens_network"]["refresh_token"] = self.refresh_token
            if self.access_token:
                data["tokens_network"]["access_token"] = self.access_token
            
            # Escritura atómica: escribir en .tmp y luego renombrar
            # Si la luz se corta durante la escritura, el archivo original queda intacto
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
                f.flush()
                os.fsync(f.fileno())  # Forzar escritura a disco
            
            # Renombrar es atómico en la mayoría de sistemas de archivos
            os.replace(tmp_file, SESSION_FILE)
                
            logger.info("✅ session_master.json actualizado atómicamente (antireinicio).")
        except Exception as e:
            logger.error(f"Error al guardar session_master.json: {e}")
            # Limpiar archivo temporal si quedó
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except:
                pass

    async def trigger_auto_extractor(self):
        logger.info("Llamando al microservicio Auto-Extractor para renovar sesión desde cero...")
        try:
            async with httpx.AsyncClient() as client:
                await client.post("http://getcid_auto_extractor:5000/start", timeout=5.0)
        except Exception as e:
            logger.error(f"Error al llamar al Auto-Extractor: {e}")

    async def refresh_access_token(self):
        if not self.refresh_token:
            logger.error("No hay refresh_token disponible. No se puede renovar.")
            return False

        # Reintentar con backoff exponencial (útil después de cortes de luz)
        max_retries = 3
        for attempt in range(max_retries):
            payload = {
                "client_id": self.client_id,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "scope": "openid profile offline_access"
            }
            
            dpop_proof = self.dpop_engine.generate_dpop_proof("POST", TOKEN_URL)
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://visualsupport.microsoft.com",
                "Referer": "https://visualsupport.microsoft.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                "DPoP": dpop_proof
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    response = await client.post(TOKEN_URL, headers=headers, data=payload)
                    
                    # Manejar DPoP-Nonce si Microsoft lo pide en el Token Endpoint
                    nonce = response.headers.get("dpop-nonce", response.headers.get("DPoP-Nonce"))
                    if nonce:
                        logger.info("Nonce detectado en Token Endpoint, reintentando...")
                        headers["DPoP"] = self.dpop_engine.generate_dpop_proof("POST", TOKEN_URL, nonce=nonce)
                        response = await client.post(TOKEN_URL, headers=headers, data=payload)
                        
                    data = response.json()
                    
                    if response.status_code == 200:
                        # El SPA usa el id_token (JWT) en lugar del access_token (que es opaco para MSA)
                        if "id_token" in data:
                            self.access_token = data["id_token"]
                            logger.info("Usando id_token como access_token.")
                        else:
                            self.access_token = data.get("access_token")
                            logger.info("No hay id_token, usando access_token normal.")
                        
                        # Extraer PUID del token (necesario para x-user-id)
                        try:
                            if self.access_token:
                                logger.info(f"Access token recibido empieza con: {self.access_token[:20]}")
                                parts = self.access_token.split('.')
                                if len(parts) >= 2:
                                    payload_b64 = parts[1]
                                    payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
                                    token_data = json.loads(base64.urlsafe_b64decode(payload_b64))
                                    self.puid = token_data.get('puid', '')
                                    logger.info(f"PUID extraído del token: {self.puid}")
                                else:
                                    logger.warning("El token no parece ser un JWT (no tiene puntos).")
                        except Exception as e:
                            logger.error(f"Error extrayendo PUID: {e}")
                            self.puid = ""
                            
                        # Actualizar el refresh token si nos dieron uno nuevo
                        if "refresh_token" in data:
                            self.refresh_token = data["refresh_token"]
                        
                        self._save_session()
                            
                        returned_type = data.get("token_type", "unknown")
                        logger.info(f"Token renovado exitosamente (Silencioso). Tipo: {returned_type}")
                        return True
                    else:
                        logger.error(f"Error al renovar token: {response.status_code} - {response.text}")
                        # Comprobar si el token está muerto permanentemente
                        if data.get("error") == "invalid_grant":
                            logger.error("CRÍTICO: El refresh_token ha sido revocado o expiró permanentemente.")
                            self.daemon_status = "failed"
                            self.daemon_error = "invalid_grant - Refresh token revocado"
                            self.refresh_token = None
                            self.access_token = None
                            asyncio.create_task(self.trigger_auto_extractor())
                            return False  # No reintentar, es permanente
                        
                        # Para otros errores HTTP, reintentar
                        if attempt < max_retries - 1:
                            wait = (attempt + 1) * 15  # 15s, 30s, 45s
                            logger.warning(f"Reintentando renovación en {wait}s (intento {attempt + 1}/{max_retries})...")
                            await asyncio.sleep(wait)
                            continue
                        return False
                except Exception as e:
                    logger.error(f"Excepción durante la petición HTTP (intento {attempt + 1}): {str(e)}")
                    if attempt < max_retries - 1:
                        wait = (attempt + 1) * 15
                        logger.warning(f"Reintentando en {wait}s tras error de red...")
                        await asyncio.sleep(wait)
                        continue
                    return False
        return False

    async def start_daemon(self):
        logger.info("🔄 Demonio de renovación de token iniciado (con antireinicio).")
        self.daemon_status = "running"
        
        # === ANTIREINICIO: Renovar token INMEDIATAMENTE al arrancar ===
        # Esto es crítico para cortes de luz: en cuanto vuelve la energía,
        # el sistema obtiene un token fresco sin esperar horas.
        if self.refresh_token:
            logger.info("⚡ Renovación inmediata al arrancar (antireinicio post-corte de luz)...")
            success = await self.refresh_access_token()
            if success:
                logger.info("✅ Token renovado exitosamente al arrancar. Sistema listo.")
            else:
                logger.warning("⚠️ Fallo en renovación al arrancar. Se reintentará en el ciclo normal.")
        else:
            logger.warning("No hay refresh_token al arrancar. Disparando Auto-Extractor...")
            self.daemon_status = "failed"
            asyncio.create_task(self.trigger_auto_extractor())
        
        while True:
            if self.daemon_status == "failed":
                logger.warning("Demonio detenido por error crítico. Esperando nuevo session_master.json...")
                self._load_session()
                if self.refresh_token:
                    self.daemon_status = "running"
                    self.daemon_error = None
                    logger.info("Nuevo token detectado. Reanudando demonio.")
                    # Intentar renovar inmediatamente con el nuevo token
                    await self.refresh_access_token()
                else:
                    await asyncio.sleep(60) # Esperar un poco antes de volver a chequear
                    continue
            else:
                self._load_session() # Intentar recargar por si hay actualizaciones

            # Generar un Jitter (espera aleatoria entre 10 y 13 horas)
            jitter_seconds = random.randint(36000, 46800)
            logger.info(f"Próxima renovación en {jitter_seconds // 3600}h {(jitter_seconds % 3600) // 60}m.")
            await asyncio.sleep(jitter_seconds)
            
            # Renovar al despertar
            if self.refresh_token:
                await self.refresh_access_token()
            else:
                logger.info("Esperando refresh_token...")

# Instancia global para ser usada por FastAPI
auth_manager = AuthManager()

