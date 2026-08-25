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

class KeyRequest(BaseModel):
    key: str

@app.post("/api/v1/check")
async def check_key(request: KeyRequest):
    key = request.key.strip().upper()
    if len(key) != 29 or key.count("-") != 4:
        raise HTTPException(status_code=400, detail="Formato de clave inválido")

    logger.info(f"Verificando clave: {key[:5]}...")
    try:
        import subprocess
        import json
        
        # Las rutas asumen que el contenedor Linux tiene los archivos en /app/bin
        exe_path = "/app/bin/pidchecker.exe"
        config_path = "/app/bin/pkeyconfig.xrm-ms"
        
        if not os.path.exists(exe_path) or not os.path.exists(config_path):
            # Fallback para pruebas locales en Windows si aplica, 
            # pero asumiremos que estamos en Docker
            exe_path = "bin/pidchecker.exe"
            config_path = "bin/pkeyconfig.xrm-ms"
            if not os.path.exists(exe_path):
                raise HTTPException(status_code=503, detail="Motor PID Checker no encontrado")

        # Ejecutar a través de wine en Linux, o directo si es Windows
        if os.name == 'nt':
            cmd = [exe_path, key, config_path]
        else:
            cmd = ["wine", exe_path, key, config_path]
            
        # Para evitar spam de wine en stdout, redirigimos stderr
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        
        if not result.stdout:
            logger.error(f"Error de wine/pidchecker: {result.stderr}")
            raise HTTPException(status_code=500, detail="Fallo en la ejecución del motor")
            
        try:
            # pidchecker.exe imprime JSON
            data = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            logger.error(f"Respuesta inválida de pidchecker: {result.stdout}")
            raise HTTPException(status_code=500, detail="Formato de respuesta del motor inválido")
            
        data["key"] = key
        
        # Mapeo de errores y formateo final como lo espera el bot
        if data.get("is_valid"):
            data["edition"] = data.get("sku", "Unknown Edition")
            data["key_type"] = data.get("license_type", "Unknown Type")
        else:
            code = data.get("error_code", "Unknown")
            data["error_message"] = "Clave no válida o bloqueada"
            if "0xC004C017" in code:
                data["error_message"] = "Clave bloqueada geográficamente"
            elif "0xC004C008" in code:
                data["error_message"] = "Límite de activaciones"
                
        return data

    except subprocess.TimeoutExpired:
        logger.error("Timeout esperando al motor PID")
        raise HTTPException(status_code=504, detail="Tiempo agotado en la verificación")
    except Exception as e:
        logger.error(f"Error comprobando clave: {e}")
        raise HTTPException(status_code=500, detail=str(e))
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
