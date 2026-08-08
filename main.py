from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
import asyncio
import httpx
import logging
from logging.handlers import RotatingFileHandler
from auth_http import auth_manager

# Configurar logs con rotación (compartido con auth_http)
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_handler = RotatingFileHandler("logs/backend.log", maxBytes=5*1024*1024, backupCount=3)
log_handler.setFormatter(log_formatter)

logger = logging.getLogger("FastAPI")
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)

app = FastAPI(title="GETCID 2.0 - Zero-Browser API")

class PIDRequest(BaseModel):
    pid: str

@app.on_event("startup")
async def startup_event():
    # Envolver la ejecución del demonio para evitar caídas silenciosas
    async def safe_daemon():
        try:
            await auth_manager.start_daemon()
        except Exception as e:
            logger.critical(f"El demonio de renovación crasheó inesperadamente: {str(e)}")
            auth_manager.daemon_status = "failed"
            auth_manager.daemon_error = f"Fatal crash: {str(e)}"
            
    # Iniciar el demonio de renovación en segundo plano
    asyncio.create_task(safe_daemon())

@app.get("/")
async def root():
    return {"message": "GETCID 2.0 API is running."}

@app.get("/status")
async def get_status():
    return {
        "api_status": "online",
        "daemon_status": auth_manager.daemon_status,
        "daemon_error": auth_manager.daemon_error,
        "has_refresh_token": auth_manager.refresh_token is not None,
        "has_access_token": auth_manager.access_token is not None
    }

@app.post("/check_pid")
async def check_pid(request: PIDRequest):
    if not auth_manager.access_token:
        # Modo simulación o inicialización
        logger.warning("No hay token de acceso válido. Usando modo simulación temporal.")
        auth_manager.access_token = "SIMULATED_TOKEN_FOR_TESTING"

    pid = request.pid
    
    # Aquí va la URL real de Microsoft para validar el PID y obtener el CID.
    # Dado que es un secreto/restringido, se deja el template exacto para inyectar el DPoP.
    MICROSOFT_CID_ENDPOINT = "https://api.microsoft.com/cid/v1/get" # Reemplazar por la URL real
    
    # Generar la prueba DPoP específicamente para esta petición (HTM y HTU deben coincidir exactos)
    dpop_proof = auth_manager.dpop_engine.generate_dpop_proof("POST", MICROSOFT_CID_ENDPOINT)
    
    headers = {
        "Authorization": f"DPoP {auth_manager.access_token}",
        "DPoP": dpop_proof,
        "Content-Type": "application/json"
    }
    
    payload = {
        "pid": pid
    }
    
    async with httpx.AsyncClient() as client:
        try:
            # Descomentar e implementar cuando se configure el endpoint real
            # response = await client.post(MICROSOFT_CID_ENDPOINT, headers=headers, json=payload)
            # return response.json()
            
            # Simulando respuesta para el template
            return {
                "success": True, 
                "message": "Petición HTTP pura simulada correctamente",
                "pid_received": pid,
                "cid": "1234567-1234567-1234567-1234567-1234567-1234567-1234567-1234567"
            }
        except Exception as e:
             raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
