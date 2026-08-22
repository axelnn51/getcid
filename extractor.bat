@echo off
cd /d "%~dp0"
echo Ejecutando Extractor Local...
python local_extractor.py
echo.

echo =======================================
echo Subiendo nuevo token a GitHub (Para Coolify/Portainer)...
git add session_master.json
git commit -m "chore: auto-update session_master.json token"
git push origin main
echo =======================================

echo Proceso finalizado. Si hubo un error, podras leerlo arriba.
pause
