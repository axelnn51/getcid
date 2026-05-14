from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn
from core import process_iid

app = FastAPI(title="GetCID Server", description="Servidor autoalojado para obtener Confirmation IDs")

# Montar recursos estáticos y plantillas
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

class IIDRequest(BaseModel):
    iid: str

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Renderiza la interfaz web principal."""
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/getcid")
async def api_getcid(req: IIDRequest):
    """Endpoint de la API para procesar el IID."""
    result = await process_iid(req.iid)
    if result.get("success"):
        return JSONResponse(status_code=200, content=result)
    else:
        return JSONResponse(status_code=400, content=result)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
