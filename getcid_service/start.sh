#!/bin/bash
# Script de arranque para GetCID Python Service
# Usa xvfb-run para manejar automáticamente la pantalla virtual y evitar problemas de lock

echo "[STARTUP] Limpiando posibles locks de X11 de reinicios anteriores..."
rm -f /tmp/.X*-lock

echo "[STARTUP] Iniciando servidor uvicorn con xvfb-run..."

# Iniciar el servidor FastAPI envuelto en xvfb-run 
# -a busca el siguiente display disponible automáticamente si :99 está ocupado
exec xvfb-run -a --server-args="-screen 0 1280x720x24 -nolisten tcp -ac" python -m uvicorn main:app --host 0.0.0.0 --port 8000
