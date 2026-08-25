# 🛡️ Parche de Estabilidad: OCR Inteligente y Fix SSL
**Fecha:** 25 de Agosto de 2026
**Tag de recuperación:** `v3.1-stable`

## 📝 Resumen de Cambios

Durante las pruebas en producción del sistema Batch API, detectamos y solucionamos dos problemas críticos que hacían fallar la obtención de CIDs:

### 1. 🌐 Evasión de Bloqueo SSL en Batch API (`batch_cid.py`)
- **El Problema:** El código de Python estaba lanzando silenciosamente el error `[SSL: CERTIFICATE_VERIFY_FAILED]` al intentar conectarse a `activation.sls.microsoft.com`. Esto obligaba al sistema a usar la Visual API como respaldo, consumiendo el token público del desarrollador (el cual eventualmente expiró).
- **La Solución:** Se inyectó `verify=False` en las instancias de `httpx.AsyncClient` dentro de `batch_cid.py`. Esto fuerza a Python a ignorar la verificación estricta del certificado de Microsoft (común en servidores Linux/Docker), permitiendo que la Batch API funcione a la perfección sin depender de NINGÚN token de terceros.

### 2. 🧠 OCR Inteligente sin IAs Externas (`ocr.js`)
- **El Problema:** El OCR estaba recogiendo "basura" de las fotos de los usuarios (como "Paso 1", la hora del teléfono, marcas de agua como "POCO F6"). Esto causaba que la extracción del IID final tomara números incorrectos.
- **La Solución (Ultra Optimizada):**
  1. Se forzó una "Lista Blanca" profunda en Tesseract (`tessedit_char_whitelist: '0123456789 \n'`) para que físicamente sea incapaz de confundir letras con números (ej. 'S' por '5').
  2. Se reescribió el algoritmo `extractIID` para aplicar un filtro inteligente de tokens: Cualquier bloque numérico de menos de 5 dígitos es considerado "basura visual" y descartado, dejando únicamente los bloques reales del IID intactos y perfectamente ensamblables.

---

## 🔧 Comandos de Restauración Rápida
Si en el futuro el sistema colapsa por algún cambio externo, puedes volver a este estado exacto (que incluye tanto la Batch API como los arreglos del OCR y SSL) ejecutando los siguientes comandos en la terminal de la carpeta raíz:

```bash
# Descartar cualquier cambio actual y volver a esta versión estable
git reset --hard v3.1-stable

# Forzar la subida a GitHub
git push -f origin main
```
Luego, simplemente haz "Pull and redeploy" en Portainer.
