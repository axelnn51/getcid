@echo off
title GetCID - Renovar Token
echo.
echo ============================================
echo   GetCID - Renovar Token de Microsoft
echo   Solo resuelve el CAPTCHA. Todo lo demas
echo   es automatico.
echo ============================================
echo.

cd /d "%~dp0"

REM Verificar si existe venv local
if exist "venv\Scripts\python.exe" (
    echo Usando Python del venv local...
    venv\Scripts\python.exe renovar_token.py
) else if exist "getcid_service\venv\Scripts\python.exe" (
    echo Usando Python del venv de getcid_service...
    getcid_service\venv\Scripts\python.exe renovar_token.py
) else (
    echo Usando Python del sistema...
    python renovar_token.py
)

echo.
echo Presiona cualquier tecla para cerrar...
pause >nul
