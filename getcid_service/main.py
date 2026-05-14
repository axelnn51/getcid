from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
from core import process_iid

app = FastAPI(title="GetCID API Server", description="Servidor interno para obtener Confirmation IDs")

class IIDRequest(BaseModel):
    iid: str

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

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
