#!/bin/bash
set -e

# Iniciar window manager y VNC en background
export DISPLAY=:99
Xvfb :99 -screen 0 1280x800x24 &
sleep 2

fluxbox &
x11vnc -display :99 -nopw -listen localhost -xkb -ncache 10 -ncache_cr -forever &

# Iniciar noVNC en el puerto 6080 web
/usr/share/novnc/utils/novnc_proxy --vnc localhost:5900 --listen 6080 &

# Iniciar la API FastAPI
echo "Iniciando Auto Extractor API en puerto 5000..."
exec uvicorn server:app --host 0.0.0.0 --port 5000
