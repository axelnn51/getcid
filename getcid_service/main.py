from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
import json
import time
import os
from core import process_iid

TOKEN_CACHE_FILE = "ms_token.json"

app = FastAPI(title="GetCID API Server", description="Servidor interno para obtener Confirmation IDs")

class IIDRequest(BaseModel):
    iid: str

class TokenRequest(BaseModel):
    token: str
    duration: int = 3600  # Duración en segundos (default: 1 hora)

@app.post("/api/getcid")
async def api_getcid(req: IIDRequest):
    """Endpoint de la API para procesar el IID."""
    try:
        import traceback
        result = await process_iid(req.iid)
        if result.get("success"):
            return JSONResponse(status_code=200, content=result)
        else:
            return JSONResponse(status_code=400, content=result)
    except Exception as e:
        error_trace = traceback.format_exc()
        return JSONResponse(status_code=500, content={"success": False, "error": f"CRITICAL PYTHON ERROR: {str(e)}\n{error_trace}"})

@app.post("/api/settoken")
async def set_token(req: TokenRequest):
    """Recibe un token de Microsoft generado localmente y lo guarda en caché."""
    try:
        expires_at = time.time() + req.duration
        with open(TOKEN_CACHE_FILE, 'w') as f:
            json.dump({
                'token': req.token,
                'expires_at': expires_at
            }, f)
        
        minutes_left = req.duration // 60
        return JSONResponse(status_code=200, content={
            "success": True, 
            "message": f"Token guardado exitosamente. Válido por {minutes_left} minutos.",
            "expires_at": expires_at
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/api/token-status")
async def token_status():
    """Devuelve el estado actual del token cacheado."""
    if not os.path.exists(TOKEN_CACHE_FILE):
        return {"status": "no_token", "message": "No hay token guardado."}
    
    try:
        with open(TOKEN_CACHE_FILE, 'r') as f:
            data = json.load(f)
        
        expires_at = data.get('expires_at', 0)
        remaining = expires_at - time.time()
        
        if remaining > 0:
            return {
                "status": "valid",
                "remaining_seconds": int(remaining),
                "remaining_minutes": int(remaining // 60),
                "message": f"Token válido por {int(remaining // 60)} minutos más."
            }
        else:
            return {
                "status": "expired",
                "expired_ago_seconds": int(abs(remaining)),
                "message": f"Token expiró hace {int(abs(remaining) // 60)} minutos."
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
