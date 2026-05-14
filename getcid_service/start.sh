#!/bin/bash
# Script de arranque para GetCID Python Service
# Inicia Xvfb (pantalla virtual) y luego el servidor uvicorn

echo "[STARTUP] Iniciando Xvfb (pantalla virtual)..."

# Iniciar Xvfb en segundo plano en display :99
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp -ac &
XVFB_PID=$!

# Exportar la variable DISPLAY para que Chrome sepa dónde renderizar
export DISPLAY=:99

# Esperar a que Xvfb esté listo
sleep 2

# Verificar que Xvfb arrancó correctamente
if kill -0 $XVFB_PID 2>/dev/null; then
    echo "[STARTUP] ✅ Xvfb arrancó correctamente en display :99 (PID: $XVFB_PID)"
else
    echo "[STARTUP] ⚠️ Xvfb falló, Chrome correrá sin pantalla virtual"
fi

echo "[STARTUP] Iniciando servidor uvicorn..."

# Iniciar el servidor FastAPI
python -m uvicorn main:app --host 0.0.0.0 --port 8000
