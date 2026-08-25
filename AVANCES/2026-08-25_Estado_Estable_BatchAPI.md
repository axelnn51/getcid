# 🚀 Punto de Restauración: GETCID 3.0 (Batch API Estable)
**Fecha:** 25 de Agosto de 2026

## 📌 Estado del Sistema
El sistema ha sido migrado exitosamente de la arquitectura frágil (Playwright + Tokens + Visual API) a una arquitectura empresarial (**Batch API SOAP**).
Actualmente funciona al **100% de manera estable, sin caídas y respondiendo en ~2 segundos**.

## 🏗️ Arquitectura Actual
*   **Backend (Python/FastAPI):**
    *   `main.py`: Levanta el servidor web en el puerto 8000. Ya no usa demonios de tokens ni requiere sesiones. Expone el endpoint `/check_pid`.
    *   `batch_cid.py`: Es el corazón del sistema. Contiene la lógica para generar firmas criptográficas `HMAC-SHA256` y comunicarse directamente con `activation.sls.microsoft.com` usando el protocolo SOAP. Tiene un sistema de emergencia (Fallback) hacia la Visual API si por algún motivo la Batch API es bloqueada.
*   **Frontend (Node.js/Telegram/Web):**
    *   `bot.js`: Bot de Telegram. Mantiene la lógica de comandos y OCR. Sigue intacto.
    *   `index.js`: Servidor web que expone las rutas para `getcid.cdkeysperu.com`. Maneja la base de datos local y descuenta créditos de WooCommerce.
    *   `cid_helper.js`: Es el puente entre el frontend de Node y el backend de Python. Pasa el `IID` a `http://getcid_backend:8000/check_pid` y recibe el CID.
    *   `ocr.js`: Lógica de Tesseract para extraer números de imágenes.

## 🗑️ Lo que se eliminó (y NO debe volver)
Se limpiaron más de 20 archivos obsoletos que causaban inestabilidad:
*   La carpeta `extractor_service` entera (Playwright consumía mucha memoria y Microsoft lo detectaba y bloqueaba).
*   `auth_http.py` y `session_master.json` (El manejo de tokens de Microsoft era propenso a errores porque expiraban o pedían Captcha).
*   Múltiples scripts de pruebas (`test_*.py`, `capture.bat`).

## 🛡️ ¿Por qué esto es un Punto de Restauración?
Si en el futuro intentas agregar nuevas funcionalidades (ej. comandos de Telegram más complejos, o nueva interfaz web) y algo se rompe, **este es el punto exacto al que debes volver.**
Se ha creado un "Tag" en Git llamado `v3.0-stable` para que puedas volver aquí con un solo comando en caso de emergencia.

## 🔙 Cómo volver a este punto (en caso de desastre)
Si rompes el código en los próximos días y quieres volver exactamente a cómo estaba hoy:
1. Abre tu terminal.
2. Ejecuta: `git checkout v3.0-stable`
3. Si quieres descartar permanentemente los cambios malos, ejecuta: `git reset --hard v3.0-stable` y luego `git push -f origin main`.
