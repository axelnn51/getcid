# Avances: Mejoras Definitivas en OCR (28 de Agosto, 2026)

## Resumen del Hito
Se ha solucionado exitosamente el problema crítico de lectura de *Installation IDs (IID)* en fotografías de pantallas con alta degradación visual (desenfoque, ruido de moiré, ángulos extraños), logrando una precisión del **100% (7/7)** en la batería de pruebas local y de producción.

## Solución Arquitectónica: Global OCR First, ROI Second
Se reemplazó la heurística problemática de "ventana deslizante" que generaba candidatos inválidos, adoptando una arquitectura estricta y modular:

1. **Fase 1 (Fast Global OCR)**: `PSM 11` sobre la imagen completa, manteniendo saltos de línea para buscar bloques perfectos de 63 o 54 dígitos.
2. **Fase 2 (Localización ROI)**: Uso de agrupamiento espacial por centroide (Y) y medianas para aislar perfectamente la línea numérica y descartar textos descriptivos.
3. **Fase 3 (OCR de ROI)**: OCR estricto (`PSM 7`) sobre la ROI aislada, con escalado cúbico y filtros Otsu.
4. **Fase 4 (Rescue - Fotografía Degradada)**: En lugar de usar métodos de consenso arriesgados o "frankensteins", se introdujo el escalado **Lanczos-4 (2.5x)** combinado con *Threshold Otsu*. Este filtro específico logró agrupar ópticamente los números borrosos permitiendo que Tesseract los interprete como un token unificado de 63 caracteres perfectos sin inventar dígitos.

## Corrección en Producción (Docker)
Se detectó una discrepancia de precisión entre el entorno Windows (local) y el entorno Linux (producción). 
- **Causa**: El paquete `tesseract-ocr` de Debian instalaba por defecto el modelo de lenguaje "Fast" (~4MB), el cual carece de la red neuronal robusta necesaria para imágenes distorsionadas.
- **Solución**: Se modificó el `Dockerfile` para descargar e inyectar automáticamente el modelo **`tessdata_best` (~23MB)** desde el repositorio oficial de GitHub, garantizando paridad exacta de lectura entre desarrollo y producción.

## Limpieza del Proyecto
- Se movieron todos los binarios locales, scripts de testeo y baterías de imágenes a la carpeta `.local_tests_and_tools/`.
- Se corrigió el archivo `.gitignore` para prevenir subidas accidentales de basura de compilación o datos locales al repositorio principal.
