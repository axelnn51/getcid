# Avances del Proyecto: OCR Anti-Moiré y Fail-Fast Inmediato de Checksum
**Fecha:** 06 de Septiembre de 2026  
**Autor:** Antigravity AI + Axel  
**Componentes Afectados:** `ocr_api.py`, `batch_cid.py`, `frontend/ocr.js`

---

## 1. Contexto y Problema Detectado

Durante pruebas en producción con fotografías tomadas directamente a **pantallas de monitores físicos LCD** (`media_1788671113397.jpg`), se detectaron dos anomalías críticas:

1. **Lectura errónea del primer dígito (`1` interpretado como `4`):**
   - **En pantalla:** `1071306 8116185 1320245 3551331 4511614 3583865 9812216 4864692 9305685`
   - **Detectado por el bot:** `4071306-8116185-1320245-3551331-4511614-3583865-9812216-4864692-9305685`
   - Los 8 bloques restantes (56 dígitos) fueron 100% correctos, pero al fallar el primer dígito, Microsoft rechazó la activación por checksum inválido.
2. **Latencia excesiva antes de mostrar el error (~30 a 40 segundos):**
   - El bot demoraba medio minuto en informar al usuario del error de checksum.

---

## 2. Diagnóstico de Causa Raíz

### A. Efecto Moiré y Ringing de Lanczos4
En `ocr_api.py`, todas las transformaciones de escala usaban `cv2.INTER_LANCZOS4`. La interpolación Lanczos tiene lóbulos negativos (función sinc) que generan sobreimpulsos ("ringing"). En fotos de monitores físicos donde la cuadrícula de subpíxeles RGB interactúa con el sensor de la cámara (efecto moiré), estos rebotes crearon una pequeña unión horizontal artificial entre el serif superior del número `1` y su tallo vertical. Tesseract interpretó esa unión como un `4`.

### B. Reintentos Inútiles en IID Matemáticamente Inválido
Cuando Microsoft SLS evalúa un IID con checksum inválido, el Batch API responde en apenas **0.8 a 1.1 segundos** con código `0x90` (*Installation ID inválido*).  
Sin embargo, `batch_cid.py` no abortaba: llamaba inútilmente a la *Visual API* comunitaria (esperando timeouts de 15 a 20s) y luego a la *WebAct API*. Como ningún servicio de activación puede validar un IID matemáticamente corrupto, estos reintentos eran 100% tiempo muerto. Además, `frontend/ocr.js` disparaba un reintento de modo rescate que volvía a llamar al backend.

---

## 3. Solución Implementada

### 1. Fail-Fast en `batch_cid.py`
Se implementó detección inmediata de errores no recuperables (`0x90` = IID inválido, `0x67` = clave bloqueada, etc.):
```python
fatal_batch_codes = ["0x90", "0x67", "0x86", "0xC004C017"]
if batch_result.get("error_code") in fatal_batch_codes:
    err_code = batch_result["error_code"]
    err_msg = batch_result.get("error_message") or BATCH_ERROR_CODES.get(err_code, "Error fatal de activación")
    logger.warning(f"[{clean_iid[:12]}...] Error fatal no recuperable ({err_code}: {err_msg}). Abortando fallbacks.")
    return {
        "success": False,
        "cid": None,
        "formatted_cid": None,
        "error_code": err_code,
        "error_message": err_msg,
        "method": "batch_api",
    }
```
* **Resultado:** La respuesta ante un checksum o IID incorrecto pasó de **~40s a 0.80s**.

### 2. Escalado Híbrido Anti-Moiré en `ocr_api.py`
Se combinaron lo mejor de dos mundos en la preparación de imágenes:
- **`scaled_raw_15` (1.5x):** Utiliza `cv2.INTER_LINEAR` para neutralizar el efecto moiré de cuadrícula y evitar el ringing en fotos de pantallas.
- **`scaled_raw_20`, `scaled_clahe` y `scaled_25` (2.0x y 2.5x):** Mantienen `cv2.INTER_LANCZOS4` para otorgar máxima nitidez y separación en capturas de pantalla pequeñas o de baja resolución.

### 3. Exposición de Candidatos Multi-Tier
En todas las salidas exitosas de `ocr_api.py`, se retorna ahora la lista completa de `candidates` ordenados por frecuencia/votos. Si el candidato 1 fallase por un dígito ambiguo, el frontend (`ocr.js`) prueba de inmediato el candidato 2 sin demoras.

---

## 4. Resultados de Verificación

Se ejecutó la suite completa de pruebas:
1. **Prueba de Fail-Fast Checksum:**  
   - Tiempo de respuesta: **0.80s** con código `0x90` y aborto limpio de fallbacks.
2. **Prueba en Foto de Monitor Físico (`media_1788671113397.jpg`):**  
   - IID extraído: `107130681161851320245355133145116143583865981221648646929305685` (100% exacto).
   - CID obtenido exitosamente: `036064-302593-359814-536634-507064-432443-791396-246954`.
3. **Suite Oficial de Benchmark (9 Imágenes):**  
   - **9/9 correctos (100% Winrate)** en 9.11 segundos totales (promedio **1.01s por imagen**).
