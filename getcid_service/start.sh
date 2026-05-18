#!/bin/bash
# Script de arranque para GetCID Python Service

echo "[STARTUP] Limpiando posibles locks de X11 de reinicios anteriores..."
rm -rf /tmp/.X*-lock
rm -rf /tmp/.X11-unix/X99

echo "[STARTUP] Iniciando Xvfb (pantalla virtual)..."
# Iniciar Xvfb en segundo plano
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp -ac &
XVFB_PID=$!

export DISPLAY=:99
export PYTHONUNBUFFERED=1

sleep 2

if kill -0 $XVFB_PID 2>/dev/null; then
    echo "[STARTUP] ✅ Xvfb arrancó correctamente en display :99 (PID: $XVFB_PID)"
else
    echo "[STARTUP] ❌ Xvfb falló al iniciar. Revisa los logs arriba."
fi

echo "[STARTUP] Iniciando servidor uvicorn..."
exec python -u -m uvicorn main:app --host 0.0.0.0 --port 8000
