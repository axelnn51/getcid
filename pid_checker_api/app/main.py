from fastapi import FastAPI, Request, HTTPException
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel
import re

from app.services.ms_soap import check_mak_activations
from app.services.pidgen import get_key_info

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="PID Checker API", version="1.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

class CheckRequest(BaseModel):
    key: str

class CheckResponse(BaseModel):
    key: str
    is_valid: bool
    edition: str | None = None
    key_type: str | None = None  # Retail, OEM, Volume:MAK, KMS
    error_code: str | None = None
    remaining_activations: int | None = None
    total_activations: int | None = None

def validate_key_format(key: str) -> bool:
    pattern = r'^([A-Z0-9]{5}-){4}[A-Z0-9]{5}$'
    return bool(re.match(pattern, key.upper()))

@app.post("/api/v1/check", response_model=CheckResponse)
@limiter.limit("10/minute") # Limite básico para evitar bloqueos
async def check_license(request: Request, payload: CheckRequest):
    key = payload.key.upper().strip()
    
    if not validate_key_format(key):
        raise HTTPException(status_code=400, detail="Formato de clave inválido. Debe ser XXXXX-XXXXX-XXXXX-XXXXX-XXXXX")

    # 1. Identificar Tipo de Clave y Edición (Lógica Pidgenx)
    key_info = get_key_info(key)
    
    if not key_info["is_valid"]:
        return CheckResponse(
            key=key,
            is_valid=False,
            error_code=key_info.get("error_code", "INVALID_KEY")
        )

    response = CheckResponse(
        key=key,
        is_valid=True,
        edition=key_info["edition"],
        key_type=key_info["type"]
    )

    # 2. Conectar a Microsoft SOAP si es MAK o para verificar estado
    if key_info["type"] == "Volume:MAK":
        mak_status = await check_mak_activations(key)
        
        if mak_status["success"]:
            response.remaining_activations = mak_status["remaining"]
            response.total_activations = mak_status["total"]
        else:
            response.is_valid = False
            response.error_code = mak_status.get("error_code") # Ej: 0xC004C003 (Bloqueada) o 0xC004C008 (Límite)

    return response

@app.get("/health")
async def health_check():
    return {"status": "ok"}
