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
        if auth_manager.refresh_token:
            logger.info("No hay access_token, pero hay refresh_token. Solicitando uno nuevo a Microsoft...")
            success = await auth_manager.refresh_access_token()
            if not success:
                raise HTTPException(status_code=401, detail="Fallo al renovar el token de acceso. Suba un nuevo session_master.json válido.")
        else:
            raise HTTPException(status_code=401, detail="Sistema no inicializado. Faltan tokens.")

    pid = request.pid.replace(" ", "").replace("-", "")
    
    import time
    import uuid
    import re

    if len(pid) not in [54, 63] or not pid.isdigit():
        raise HTTPException(status_code=400, detail="El IID debe tener exactamente 54 o 63 dígitos numéricos.")

    MICROSOFT_CID_ENDPOINT = "https://visualsupport.microsoft.com/api/productActivation/validateIID"
    htu = "/api/productActivation/validateIID"
    htm = "POST"
    sid = f"app_{int(time.time() * 1000)}_{str(uuid.uuid4())[:8]}"
    digits = len(pid) // 9
    dfp_session_id = str(uuid.uuid4())
    req_id = str(uuid.uuid4()).replace("-", "")
    trace_id = str(uuid.uuid4()).replace("-", "")
    
    payload = {
        "IID": pid,
        "ProductType": "windows",
        "productGroup": "Windows",
        "productName": "Windows 11",
        "numberOfDigits": digits,
        "Country": "MEX",
        "Region": "LATAM",
        "dfpSessionId": dfp_session_id
    }
    
    headers = {
        "authorization": f"Bearer {auth_manager.access_token}",
        "content-type": "application/json",
        "accept": "application/json, text/plain, */*",
        "referer": "https://visualsupport.microsoft.com/activate",
        "x-session-id": sid,
        "request-id": f"|{req_id}.{trace_id[:16]}",
        "traceparent": f"00-{req_id}-{trace_id[:16]}-01",
        "x-user-id": auth_manager.puid if getattr(auth_manager, 'puid', None) else '00037FFFB13977BF',
        "sec-ch-ua-platform": '"Windows"',
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    }
    
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        try:
            # 1. Generar DPoP inicial (prueba con ruta relativa que antes no daba mismatch)
            htu = "/api/productActivation/validateIID"
            dpop_proof = auth_manager.dpop_engine.generate_dpop_proof(htm, htu)
            
            req_headers = headers.copy()
            req_headers["DPoP"] = dpop_proof
            
            logger.info(f"[{pid}] Iniciando desafío DPoP con Microsoft API...")
            response = await client.post(MICROSOFT_CID_ENDPOINT, headers=req_headers, json=payload)
            
            # 2. Manejar Nonce si Microsoft lo pide
            if "dpop-nonce" in response.headers or "DPoP-Nonce" in response.headers:
                nonce = response.headers.get("dpop-nonce", response.headers.get("DPoP-Nonce"))
                logger.info(f"[{pid}] Nonce detectado, reintentando con firma completa...")
                req_headers["DPoP"] = auth_manager.dpop_engine.generate_dpop_proof(htm, htu, nonce=nonce)
                response = await client.post(MICROSOFT_CID_ENDPOINT, headers=req_headers, json=payload)
                
            # 3. ANTIREINICIO: Si el token expiró, renovar automáticamente y reintentar
            if response.status_code in (401, 403):
                logger.warning(f"[{pid}] Token expirado (401/403). Intentando renovación automática...")
                refresh_ok = await auth_manager.refresh_access_token()
                if refresh_ok:
                    logger.info(f"[{pid}] Token renovado exitosamente. Reintentando petición CID...")
                    # Reconstruir headers con el nuevo token
                    req_headers["authorization"] = f"Bearer {auth_manager.access_token}"
                    req_headers["x-user-id"] = auth_manager.puid if getattr(auth_manager, 'puid', None) else '00037FFFB13977BF'
                    dpop_proof = auth_manager.dpop_engine.generate_dpop_proof(htm, htu)
                    req_headers["DPoP"] = dpop_proof
                    response = await client.post(MICROSOFT_CID_ENDPOINT, headers=req_headers, json=payload)
                    
                    # Manejar nonce de nuevo si lo piden
                    if "dpop-nonce" in response.headers or "DPoP-Nonce" in response.headers:
                        nonce = response.headers.get("dpop-nonce", response.headers.get("DPoP-Nonce"))
                        req_headers["DPoP"] = auth_manager.dpop_engine.generate_dpop_proof(htm, htu, nonce=nonce)
                        response = await client.post(MICROSOFT_CID_ENDPOINT, headers=req_headers, json=payload)
                    
                    if response.status_code in (401, 403):
                        logger.error(f"[{pid}] Token sigue inválido tras renovación. MS Response: {response.text}")
                        auth_manager.access_token = None
                        raise HTTPException(status_code=401, detail=f"Token inválido incluso tras renovación. Contacta al administrador.")
                else:
                    logger.error(f"[{pid}] No se pudo renovar el token. MS Response: {response.text}")
                    auth_manager.access_token = None
                    raise HTTPException(status_code=401, detail=f"Token expirado y no se pudo renovar automáticamente.")
            elif response.status_code != 200:
                logger.error(f"Error de Microsoft: {response.text}")
                raise HTTPException(status_code=500, detail=f"Error {response.status_code} en Microsoft")
                
            data = response.json()
            cid_value = data.get("cid") or data.get("CID") or data.get("confirmationId")
            
            if cid_value and isinstance(cid_value, str) and len(cid_value) >= 48:
                formatted_cid = "-".join(re.findall(r'.{6}', cid_value)) if "-" not in cid_value else cid_value
                return {"success": True, "cid": formatted_cid, "raw_cid": cid_value}
                
            if data.get("validChecksum") is False:
                return {"success": False, "message": "IID con checksum inválido."}
                
            return {"success": False, "message": f"Respuesta inesperada de MS: {data}"}
            
        except Exception as e:
            logger.error(f"Error en API de Microsoft: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/update_session")
async def update_session(session_data: dict):
    try:
        import json
        with open("session_master.json", "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=4)
        
        auth_manager._load_session()
        
        if auth_manager.refresh_token:
            auth_manager.daemon_status = "running"
            auth_manager.daemon_error = None
            asyncio.create_task(auth_manager.refresh_access_token())
            
        return {"success": True, "message": "Sesión actualizada y tokens recargados en memoria."}
    except Exception as e:
        logger.error(f"Error al actualizar sesión: {e}")
        return {"success": False, "error": str(e)}

@app.post("/api/force_extraction")
async def force_extraction():
    try:
        logger.warning("Recibida petición de extracción forzada. Vaciando sesión...")
        auth_manager.refresh_token = None
        auth_manager.access_token = None
        auth_manager.daemon_status = "failed"
        
        # Vaciar archivo físico
        with open("session_master.json", "w", encoding="utf-8") as f:
            f.write("{}")
            
        # Llamar al extractor
        asyncio.create_task(auth_manager.trigger_auto_extractor())
        return {"success": True, "message": "Extracción forzada iniciada."}
    except Exception as e:
        logger.error(f"Error en extracción forzada: {e}")
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
