from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import logging
from logging.handlers import RotatingFileHandler
import os

from batch_cid import get_cid

# Asegurar directorio de logs
os.makedirs("logs", exist_ok=True)

# Configurar logs
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_handler = RotatingFileHandler("logs/backend.log", maxBytes=5*1024*1024, backupCount=3)
log_handler.setFormatter(log_formatter)

logger = logging.getLogger("FastAPI")
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)

# Configurar logger del módulo batch_cid
batch_logger = logging.getLogger("BatchCID")
batch_logger.setLevel(logging.INFO)
batch_logger.addHandler(log_handler)
batch_logger.addHandler(console_handler)

app = FastAPI(title="GETCID 3.0 — Batch API")


class PIDRequest(BaseModel):
    pid: str


@app.get("/")
async def root():
    return {"message": "GETCID 3.0 — Batch API is running."}


@app.get("/status")
async def get_status():
    return {
        "api_status": "online",
        "version": "3.0",
        "engine": "BatchActivation SOAP + Visual API fallback",
        "requires_tokens": False,
    }


@app.post("/check_pid")
async def check_pid(request: PIDRequest):
    pid = request.pid.strip()
    logger.info(f"Recibida solicitud CID para IID: {pid[:20]}...")

    try:
        result = await get_cid(pid)
    except Exception as e:
        logger.error(f"Error inesperado en get_cid: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    if result["success"]:
        return {
            "success": True,
            "cid": result["formatted_cid"],
            "raw_cid": result["cid"],
            "method": result["method"],
        }

    # Map error types for frontend compatibility
    error_msg = result.get("error_message", "Error desconocido")
    error_code = result.get("error_code")

    return {
        "success": False,
        "error": error_msg,
        "code": error_code,
        "message": error_msg,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
