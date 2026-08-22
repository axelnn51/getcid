from fastapi import FastAPI, BackgroundTasks
import subprocess
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("extractor_server")

app = FastAPI()

def run_extraction():
    logger.info("Iniciando auto_extractor.py...")
    # Ejecutamos el script de Playwright
    # Este script usará el Display :99 que Xvfb tiene corriendo.
    subprocess.run(["python3", "auto_extractor.py"])

@app.post("/start")
async def start_extraction(background_tasks: BackgroundTasks):
    logger.info("Recibida petición para arrancar el extractor automático.")
    background_tasks.add_task(run_extraction)
    return {"status": "Extracción iniciada en background"}
