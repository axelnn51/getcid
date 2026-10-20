# 📋 Informe de Actualización y Despliegue — Portal GETCID (CDKeys Perú)

**Fecha:** 20 de Octubre de 2026  
**Proyecto:** GETCID — Portal de Activación Telefónica Automática  
**Cliente / Marca:** CDKeys Perú  
**Estado:** ✅ Completado, Verificado y Desplegado en Git (`main`)

---

## 1. Resumen de Requerimientos y Cambios Realizados

### 1.1. Guía en Ancho Completo (Centro + Espacio Lateral)
- **Situación previa:** La sección de infografía estaba dentro de la columna central (`center-col`), viéndose comprimida entre las columnas laterales de productos recomendados y populares.
- **Solución implementada:** Se reubicó la sección fuera de la cuadrícula de 3 columnas hacia un contenedor dedicado de ancho completo (`container guide-fullwidth-wrap`), abarcando los 1280px máximos del contenedor oficial. Ahora se aprovecha todo el ancho disponible por debajo del formulario generador.

### 1.2. Slider / Carrusel Deslizable ("en chiquito" y "ampliado")
- **Modo en miniatura / incrustado:**
  - Flechas de navegación Anterior (`❮`) y Siguiente (`❯`) flotantes con efectos translúcidos y acento dorado CDKeys Perú.
  - **Soporte gestual táctil (Swipe) y arrastre con mouse (Drag):** permite deslizar horizontalmente con el dedo en teléfonos y tablets o arrastrar con el cursor en PC para cambiar entre Parte 1 y Parte 2.
  - Pestañas interactivas superiores y barra de paginación inferior (`• •`) sincronizadas en tiempo real.
- **Modo ampliado (Lightbox Modal HD):**
  - Se agregaron controles de navegación lateral (`❮` y `❯`) dentro del visor a pantalla completa.
  - Soporte de **deslizamiento táctil (Swipe)** directamente en la imagen ampliada.
  - Soporte para navegación mediante las flechas del teclado (`◄` `►`), zoom dinámico (+ / - / 100%) y cierre con tecla `Escape`.

### 1.3. Paso a Paso Escrito (Breve, Claro y Entendible)
- Se incorporó debajo del slider una cuadrícula de 4 pasos estructurados:
  1. **Paso 1: Instala e ingresa tu clave:** Introduce la clave de producto de 25 caracteres en Office o Windows.
  2. **Paso 2: Elige "Activar por teléfono":** Abre el asistente de activación para obtener tu Installation ID (IID de 9 bloques).
  3. **Paso 3: Genera tu CID en este portal:** Escribe o pega tu IID (o sube tu captura con auto-OCR mediante `Ctrl + V`).
  4. **Paso 4: Coloca el CID y finaliza:** Introduce los 8 bloques (A–H) generados para activar de por vida.
- **Barra de Asistencia Inmediata:** Botón directo a WhatsApp oficial con mensaje preconfigurado para soporte humano 24/7.

### 1.4. Eliminación de Menciones a "IA"
- Se modificó la etiqueta en el formulario de carga: `(auto-OCR con IA)` ➔ `(auto-OCR)`.
- Se corrigió el indicador de escaneo: `Leyendo dígitos de la imagen con Inteligencia Artificial...` ➔ `Leyendo dígitos de la imagen automáticamente...`.
- Se limpió el mensaje del bot de Telegram en `frontend/bot.js`.

---

## 2. Archivos Modificados

| Archivo | Tipo de Cambio | Descripción |
|---|---|---|
| [`frontend/public/index.html`](frontend/public/index.html) | Estructura & Lógica | Reubicación a ancho completo, carrusel interactivo, paso a paso escrito, lightbox con navegación y eliminación de etiquetas IA |
| [`frontend/public/style.css`](frontend/public/style.css) | Estilos CSS | Grid responsivo, animaciones suaves del slider, flechas de navegación, tarjetas paso a paso y responsive móvil/tablet |
| [`frontend/bot.js`](frontend/bot.js) | Backend / Bot | Limpieza de mención "con IA" en el estado del motor OCR v3.3 |

---

## 3. Guía de Despliegue en Portainer

1. Acceder al panel de **Portainer**.
2. Ir a la sección **Stacks** o **Containers** donde corre el contenedor de `getcid`.
3. Seleccionar el stack y hacer clic en **Update the stack** / **Redeploy**.
4. Activar la opción **"Re-pull image"** (si aplica build automático) o reiniciar el contenedor para que tome el último commit de la rama `main` de GitHub:
   ```bash
   git pull origin main
   npm start
   ```

---
*Documento generado automáticamente tras la verificación del despliegue.*
