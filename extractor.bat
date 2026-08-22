@echo off
cd /d "%~dp0"
echo Ejecutando Extractor Local...
python local_extractor.py
echo.
echo =======================================
echo Proceso finalizado. Si hubo un error, podras leerlo arriba.
pause
