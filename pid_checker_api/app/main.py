import sys
import os
import logging

# Agregar winkeycheck al path de Python
sys.path.insert(0, "/app/winkeycheck")

from fastapi import FastAPI, Request, HTTPException
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel
import re
import xml.etree.ElementTree as ET

# Importar el motor real de winkeycheck
from licensing_stuff.keycutter import ProductKeyDecoder
from licensing_stuff.pkeyconfig import PKeyConfig

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="PID Checker API", version="2.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ============================================================
# Cargar pkeyconfig al iniciar la API
# ============================================================
PKEYCONFIG_PATH = os.environ.get("PKEYCONFIG_PATH", "/app/winkeycheck/pkeyconfig.xrm-ms")
pkc = None

@app.on_event("startup")
async def load_pkeyconfig():
    global pkc
    try:
        with open(PKEYCONFIG_PATH, "r", encoding="utf-8-sig") as f:
            pkc = PKeyConfig(ET.fromstring(f.read()))
        logger.info(f"✅ PKeyConfig cargado desde {PKEYCONFIG_PATH}")
    except Exception as e:
        logger.error(f"❌ Error cargando pkeyconfig: {e}")

# ============================================================
# Importar funciones de verificación real
# ============================================================
from keycheck import query_key, consume_key, PUB_LICENSE

# ============================================================
# Modelos
# ============================================================
class CheckRequest(BaseModel):
    key: str
    consume: bool = False  # Si true, prueba consumo (más agresivo, consume 1 activación si OK)

class CheckResponse(BaseModel):
    key: str
    is_valid: bool
    edition: str | None = None
    key_type: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    remaining_activations: int | None = None
    total_activations: int | None = None

# ============================================================
# Mapa de error codes a mensajes legibles
# ============================================================
ERROR_MESSAGES = {
    "0xC004C003": "Clave bloqueada por Microsoft",
    "0xC004C008": "Límite de activaciones excedido",
    "0xC004C020": "Clave MAK sin activaciones restantes",
    "0xC004C060": "Clave no válida para este producto",
    "0xC004C004": "Clave no encontrada en la base de datos de Microsoft",
    "0xC004C001": "Clave inválida",
    "0xC004C530": "Clave OEM, no se puede verificar online",
}

def validate_key_format(key: str) -> bool:
    pattern = r'^([A-Z0-9]{5}-){4}[A-Z0-9]{5}$'
    return bool(re.match(pattern, key.upper()))

def get_edition_from_pkc(key: str) -> dict:
    """Usa pkeyconfig para identificar la edición y tipo de la clave."""
    try:
        if "N" not in key:
            return {"edition": None, "key_type": None, "error": "No es PKEY2009"}
        
        pkey_data = ProductKeyDecoder(key)
        config = pkc.config_for_group(pkey_data.group)
        
        edition = getattr(config, 'edition_id', None) or getattr(config, 'product_description', None) or str(config.config_id)
        
        # Detectar tipo basado en el config
        key_type = "Retail"
        config_id_str = str(config.config_id).lower() if config.config_id else ""
        edition_str = str(edition).lower() if edition else ""
        
        if "mak" in edition_str or "volume" in edition_str or "mak" in config_id_str:
            key_type = "Volume:MAK"
        elif "oem" in edition_str:
            key_type = "OEM"
        elif "kms" in edition_str:
            key_type = "KMS"
        elif "retail" in edition_str:
            key_type = "Retail"
            
        return {"edition": edition, "key_type": key_type, "error": None}
    except Exception as e:
        return {"edition": None, "key_type": None, "error": str(e)}

# ============================================================
# Endpoints
# ============================================================
@app.post("/api/v1/check", response_model=CheckResponse)
@limiter.limit("10/minute")
async def check_license(request: Request, payload: CheckRequest):
    key = payload.key.upper().strip()
    
    if not validate_key_format(key):
        raise HTTPException(status_code=400, detail="Formato de clave inválido. Debe ser XXXXX-XXXXX-XXXXX-XXXXX-XXXXX")

    if pkc is None:
        raise HTTPException(status_code=503, detail="Motor PKeyConfig no está cargado. Reinicie el servicio.")

    # 1. Identificar edición offline
    edition_info = get_edition_from_pkc(key)
    
    # 2. Verificar online contra Microsoft
    try:
        if payload.consume:
            error_code, message, success = consume_key(key, PUB_LICENSE, pkc)
        else:
            error_code, message, success = query_key(key, pkc)
    except Exception as e:
        logger.error(f"Error verificando clave: {e}")
        return CheckResponse(
            key=key,
            is_valid=False,
            edition=edition_info.get("edition"),
            key_type=edition_info.get("key_type"),
            error_code="NETWORK_ERROR",
            error_message=str(e)
        )

    if success:
        return CheckResponse(
            key=key,
            is_valid=True,
            edition=edition_info.get("edition"),
            key_type=edition_info.get("key_type"),
            error_code=error_code  # "0x0" cuando es válida
        )
    else:
        human_message = ERROR_MESSAGES.get(error_code, message or "Error desconocido")
        return CheckResponse(
            key=key,
            is_valid=False,
            edition=edition_info.get("edition"),
            key_type=edition_info.get("key_type"),
            error_code=error_code,
            error_message=human_message
        )

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "pkeyconfig_loaded": pkc is not None,
        "engine": "winkeycheck"
    }
