# 📋 Informe de Actualización y Despliegue — Portal GETCID (CDKeys Perú)

**Fecha:** 20 de Octubre de 2026  
**Proyecto:** GETCID — Portal de Activación Telefónica Automática  
**Cliente / Marca:** CDKeys Perú  
**Estado:** ✅ Completado, Verificado y Desplegado en Git (`main`)

---

## 1. Resumen de Requerimientos y Cambios Realizados

### 1.1. Eliminación del Espacio en Blanco Superior y Nivelación de Columnas
- **Problema previo:** Al reubicar la guía visual hacia el ancho completo, la columna central (formulario generador) quedó con una altura de ~520px, mientras que las columnas laterales tenían 3 tarjetas de productos cada una (~750px). Esta disparidad dejaba un vacío en blanco de más de 200px debajo del botón *"Obtener ID de Confirmación"*.
- **Solución implementada:** 
  - Se mantuvieron las 2 licencias core de mayor rotación por columna (Windows 11 Pro y Windows 10 Pro en la izquierda; Office 2024 Pro Plus y Cuenta Office 365 en la derecha).
  - Los productos complementarios (*Canva Pro Edu* y *Nitro PDF Pro*) se transformaron en elegantes tarjetas compactas con enlace directo a la tienda.
  - La altura de las 3 columnas quedó perfectamente nivelada (~525px), eliminando por completo cualquier espacio en blanco innecesario y logrando una transición simétrica hacia la guía visual inferior.

### 1.2. Actualización de la Infografía Parte 2
- **Nueva imagen oficial:** Se actualizó la infografía de la Parte 2 al nuevo recurso oficial en alta resolución:
  `https://cdkeysperu.com/wp-content/uploads/2026/10/Guiade-activacion-telefonica-de-Office-pt2.png`
- **Fallback local optimizado:** Se actualizó tanto la referencia en el carrusel principal como en el visor Lightbox HD y el archivo de respaldo local `guia-office-pt2.webp`.

### 1.3. Aviso Destacado en Rojo: Verificación con Reinicio y Políticas de Garantía
- Al término de la cuadrícula de pasos escritos, se incorporó un panel de advertencia de alta visibilidad (`warranty-alert-card`) en rojo/carmesí oficial que especifica con máxima claridad:
  1. **🔄 Verificación Obligatoria (Reinicio de Office):** Instrucción indispensable de cerrar por completo Word, Excel o PowerPoint y volver a abrirlos (o reiniciar la PC) para constatar en *Archivo > Cuenta* que figure el estado en verde *"Producto activado"*.
  2. **🛡️ Garantía de Licencia Activada (1 Año):** Una vez activado el software en el equipo, el cliente cuenta con 1 año de garantía y soporte oficial (la licencia permanece activa siempre y cuando no se desinstale el programa, no se borre Office ni se formatee la máquina).
  3. **⏳ Garantía de Claves Sin Activar / Guardadas (30 Días):** Para clientes que guardan la clave sin usar, se estipula un plazo máximo de 30 días de garantía desde la compra para canjearla en el portal. Pasados los 30 días, la clave pierde la cobertura de canje inicial.

---

## 2. Archivos Modificados

| Archivo | Tipo de Cambio | Descripción |
|---|---|---|
| [`frontend/public/index.html`](frontend/public/index.html) | Estructura & Contenido | Nivelación de columnas, mini links de Canva/Nitro, nueva imagen Parte 2 y banner de garantía/reinicio en rojo |
| [`frontend/public/style.css`](frontend/public/style.css) | Estilos CSS | Estilos de `.product-mini-link`, eliminación de margen sobrante en `.card-main` y estilos responsivos de `.warranty-alert-card` |
| [`frontend/public/images/guia-office-pt2.webp`](frontend/public/images/guia-office-pt2.webp) | Assets | Actualización del asset local con la nueva infografía oficial de Parte 2 |

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
*Documento generado tras la verificación visual y funcional del portal.*
