#!/bin/bash
# Script de arranque hardened para GetCID Python Service
# Anti-caídas: limpia zombies, verifica Xvfb, y hace cleanup agresivo

set -o pipefail

echo "[STARTUP] ══════════════════════════════════════════════"
echo "[STARTUP] 🚀 GetCID Python Service — Arranque Anti-Caídas"
echo "[STARTUP] ══════════════════════════════════════════════"

# ─── PASO 0: Matar CUALQUIER proceso huérfano de runs anteriores ───
echo "[STARTUP] 🧹 Limpiando procesos huérfanos de Chrome/Xvfb..."
pkill -9 -f "chrome" 2>/dev/null || true
pkill -9 -f "chromium" 2>/dev/null || true
pkill -9 -f "Xvfb" 2>/dev/null || true
# Esperar a que mueran completamente
sleep 1

# Limpiar locks de X11 de reinicios anteriores
rm -rf /tmp/.X*-lock 2>/dev/null || true
rm -rf /tmp/.X11-unix/X99 2>/dev/null || true
# Limpiar archivos temporales de Chrome acumulados (pueden ser GB)
rm -rf /tmp/getcid_chrome_* 2>/dev/null || true
rm -rf /tmp/.org.chromium.Chromium* 2>/dev/null || true
rm -rf /tmp/playwright* 2>/dev/null || true
echo "[STARTUP] ✅ Limpieza de huérfanos completada."

# ─── PASO 1: Crear estructura de directorios persistentes ───
echo "[STARTUP] Creando estructura de directorios persistentes..."
mkdir -p /app/persist/states
mkdir -p /app/persist/chrome_profile
chmod -R 777 /app/persist 2>/dev/null || true

# ─── PASO 2: Verificar estado de tokens ───
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

# ─── PASO 3: Iniciar Xvfb con reintentos ───
echo "[STARTUP] Iniciando Xvfb (pantalla virtual)..."

XVFB_STARTED=false
for attempt in 1 2 3; do
    # Limpiar locks antes de cada intento
    rm -rf /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true
    
    Xvfb :99 -screen 0 1280x720x24 -nolisten tcp -ac &
    XVFB_PID=$!
    sleep 2
    
    if kill -0 $XVFB_PID 2>/dev/null; then
        echo "[STARTUP] ✅ Xvfb arrancó correctamente en display :99 (PID: $XVFB_PID) — intento $attempt"
        XVFB_STARTED=true
        break
    else
        echo "[STARTUP] ⚠️ Xvfb falló en intento $attempt/3. Reintentando..."
        sleep 1
    fi
done

if [ "$XVFB_STARTED" = false ]; then
    echo "[STARTUP] ❌ Xvfb falló después de 3 intentos. Playwright podría no funcionar."
    echo "[STARTUP] ⚠️ Continuando de todas formas (el refresh token funciona sin Xvfb)..."
fi

export DISPLAY=:99
export PYTHONUNBUFFERED=1
export GETCID_SERVER="http://localhost:8000"

# ─── PASO 4: Reportar uso de recursos ───
echo "[STARTUP] ── Recursos del sistema ──"
echo "[STARTUP] RAM: $(free -h 2>/dev/null | awk 'NR==2{print $3"/"$2}' || echo 'N/A')"
echo "[STARTUP] Disco /app/persist: $(du -sh /app/persist 2>/dev/null | cut -f1 || echo 'N/A')"
echo "[STARTUP] Procesos Chrome: $(pgrep -c chrome 2>/dev/null || echo '0')"
echo "[STARTUP] ─────────────────────────"

echo "[STARTUP] 🚀 Iniciando servidor uvicorn..."
exec python -u -m uvicorn main:app --host 0.0.0.0 --port 8000 --loop asyncio
