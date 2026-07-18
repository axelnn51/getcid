#!/bin/bash
# Script de arranque para GetCID Python Service

echo "[STARTUP] Limpiando posibles locks de X11 de reinicios anteriores..."
rm -rf /tmp/.X*-lock
rm -rf /tmp/.X11-unix/X99

echo "[STARTUP] Creando estructura de directorios persistentes..."
mkdir -p /app/persist/states
mkdir -p /app/persist/chrome_profile
chmod -R 777 /app/persist 2>/dev/null || true

echo "[STARTUP] ── Estado de tokens persistentes ──"
if [ -f "/app/persist/ms_token.json" ]; then
    echo "[STARTUP] ✅ ms_token.json ENCONTRADO"
    python3 -c "
import json, time
with open('/app/persist/ms_token.json') as f: d = json.load(f)
remaining = d.get('expires_at', 0) - time.time()
print(f'[STARTUP]    Expira en: {int(remaining//60)} minutos' if remaining > 0 else '[STARTUP]    ⚠️  TOKEN EXPIRADO')
" 2>/dev/null || echo "[STARTUP]    (no se pudo leer)"
else
    echo "[STARTUP] ❌ ms_token.json NO encontrado — se necesitará renovación"
fi

if [ -f "/app/persist/ms_refresh_token.json" ]; then
    echo "[STARTUP] ✅ ms_refresh_token.json ENCONTRADO"
else
    # Migración automática: si existe con nombre de backup, renombrarlo
    for backup_name in ms_refresh_token_NUEVO.json ms_refresh_token_backup.json; do
        if [ -f "/app/persist/$backup_name" ]; then
            echo "[STARTUP] ⚠️ Encontrado $backup_name — migrando a ms_refresh_token.json..."
            cp "/app/persist/$backup_name" "/app/persist/ms_refresh_token.json"
            echo "[STARTUP] ✅ Migración completada."
            break
        fi
    done
    if [ ! -f "/app/persist/ms_refresh_token.json" ]; then
        echo "[STARTUP] ❌ ms_refresh_token.json NO encontrado — se lanzará renovación vía Playwright"
    fi
fi
echo "[STARTUP] ───────────────────────────────────"

echo "[STARTUP] Iniciando Xvfb (pantalla virtual)..."
# Iniciar Xvfb en segundo plano
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp -ac &
XVFB_PID=$!

export DISPLAY=:99
export PYTHONUNBUFFERED=1
export GETCID_SERVER="http://localhost:8000"

sleep 2

if kill -0 $XVFB_PID 2>/dev/null; then
    echo "[STARTUP] ✅ Xvfb arrancó correctamente en display :99 (PID: $XVFB_PID)"
else
    echo "[STARTUP] ❌ Xvfb falló al iniciar. Revisa los logs arriba."
fi

echo "[STARTUP] Iniciando servidor uvicorn..."
exec python -u -m uvicorn main:app --host 0.0.0.0 --port 8000

