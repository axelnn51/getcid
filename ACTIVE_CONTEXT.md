# 📍 ACTIVE_CONTEXT: GETCID Final (Portal y Backend Activación Licencias)
> Última Actualización: 2026-11-10 | Versión: Estable v4.3 | Estado: ✅ PUSHEADO A GITHUB Y LISTO PARA PORTAINER

## 1. 🚦 Estado Actual Inmediato
- **Servicio Web:** `getcid.cdkeysperu.com` (Frontend simétrico de 3 columnas alineado a 525px).
- **Backend / DB:** Node.js + Express con base de datos SQLite (`order_credits`, `product_presets` con `guide_url`, `key_stock`).
- **Bot de Telegram:**
  - Generador genérico (`/pedido <créditos> [producto]`, `/pedidos`): usa botón nativo `copy_text` (`<= 256` caracteres).
  - Atajos rápidos con stock local FIFO + Guía integrada: `/pedido21` (`Office Pro Plus 2021` + `office-2021`) y `/pedido24` (`Office Pro Plus LTSC 2024` + `office-2024`), ampliable con `/addprod <cod> <Nombre> | [url_guia]`. También `/guia21`, `/guia24` y `/guia`.
  - Cola local FIFO persistente en SQLite (`/app/data/getcid.db` en volumen `./frontend/data`): cargar con `/cargar24`, `/cargar21` o botones de `/stock` (cada `ENTER` = 1 key; permite keys repetidas o únicas; consume 1 key por crédito en estricto orden `ORDER BY id ASC`).
  - Flujo de entrega WhatsApp para presets con guía (`/pedido21`, `/pedido24`):
    - **Mensaje 1 (Admin):** Resumen del pedido + stock restante (y botón rápido de recarga si llega a 0).
    - **Mensaje 2 (Cliente 1-Clic Completo):** Bloque `<pre><code class="language-WhatsApp">...</code></pre>` con el mensaje unificado exacto (462–491 chars: Pedido + Key FIFO o `🔑 Key: ` vacío + separador + Guía completa 2021/2024 con `*negritas*` de WhatsApp) para copiar **TODO en 1 clic** (superando el límite de 256c de `copy_text`), más botón `[ 💬 Enviar Completo por WhatsApp ]` (`wa.me/?text=`).
- **Lógica GETCID / OCR:** **100% intacta**, sin modificaciones en `index.js`, `cid_helper.js`, `ocr.js`, `main.py`, `bot.on('photo')` ni `handleDirectIID`.
- **Git Push:** Sincronizado a `https://axelnn51@github.com/axelnn51/getcid.git`.

---

## 2. 🧠 Base de Conocimiento de Errores (Caminos Resueltos vs Descartados)

### ✅ Errores Resueltos Definitivamente (No volver a diagnosticar)
1. **Falso Disparo de Soporte WhatsApp por Campo Vacío:**
   - *Causa:* Si el usuario escaneaba un IID y enviaba el formulario sin correo o pedido, `showErr()` asumía que Microsoft había rechazado la clave y abría la asistencia de WhatsApp.
   - *Solución:* Validación visual inline previa al envío (`.input-error` y `.field-error-tip`). La caja de WhatsApp `#error-help` queda reservada estrictamente para rechazos reales de la API de Microsoft.
2. **Colisión de IDs entre Ventas Telegram y WooCommerce:**
   - *Causa:* Riesgo de colisión si un pedido manual generado por bot tenía el mismo ID que un pedido automático de WooCommerce.
   - *Solución:* Todos los pedidos de Telegram usan el prefijo canónico `CDK-XXXXX` (ej: `CDK-70842`). Búsqueda en web tolerante a mayúsculas, minúsculas o solo dígitos. Probabilidad de colisión: 0%.
3. **Desbalance de Layout y Hueco Blanco Vertical:**
   - *Causa:* Las columnas laterales tenían 3 productos (~750px) mientras la central medía ~520px.
   - *Solución:* Productos secundarios (*Canva* y *Nitro*) convertidos a `.product-mini-link` compactos horizontales. Altura perfecta de 525px.
4. **Falso "No se encontró cuenta" y Bloqueo de 30 min en Lotes Multilicencia (Rate Limiting Asfixiante):**
   - *Causa:* `/api/check-balance` y `/api/portal/getcid` compartían un único `apiLimiter` con umbral ultra-estricto de 5 fallos en 30 minutos. Al procesar pedidos grandes (ej. 40 activaciones), unos pocos rechazos de Microsoft (`MS_ERROR`) o errores de OCR (`INVALID_CHECKSUM`) agotaban los 5 intentos y bloqueaban la IP. Toda posterior verificación en `index.html` recibía HTTP 429 y caía erróneamente en el `else`, mostrando al cliente: *"No se encontró cuenta con este email o pedido"*, haciendo creer que su saldo de 40 se había borrado.
   - *Solución:* 
     1) Desacoplamiento total: `/api/check-balance` tiene su propio `balanceLimiter` permisivo (60 req/min) que nunca bloquea la lectura de saldo.
     2) `/api/portal/getcid` ampliado a `portalLimiter` con hasta 30 fallos en 10 min.
     3) Frontend `index.html` distingue HTTP 429 / errores de red de un `found: false` real, mostrando mensajes contextuales precisos.
     4) Normalización robusta en `db.js` y `index.js` soportando `#`, prefijos numéricos o espacios accidentales.
5. **Copia Incompleta en PC por Texto Mixto y Bloque `<pre>` Rígido:**
   - *Causa:* El bloque `<pre>` generaba una caja negra rígida con cabecera "copy" poco estética.
   - *Solución:* Se usa `<code>` multilínea integrado en el estilo nativo de Telegram, con `disable_web_page_preview: true` para suprimir la tarjeta de enlace que ocupaba media pantalla. El bloque copia fielmente las 4 líneas solicitadas con `Portal Web CID:`.
6. **Conflicto de Credenciales Git en Windows (Colisión entre `cdkeysperu-com` y `axelnn51`):**
   - *Causa:* En Windows Credential Manager existían dos credenciales para GitHub: una de `cdkeysperu-com` y otra de `axelnn51`. Git intentaba autenticar con `cdkeysperu-com`, el cual no tenía permisos sobre `axelnn51/getcid`, recibiendo HTTP 401 y congelando el push en subshells sin GUI.
   - *Solución:* Configurar explícitamente `remote.origin.url` con `https://axelnn51@github.com/axelnn51/getcid.git` y `git config credential.username "axelnn51"`. Git selecciona de inmediato la credencial correcta de `axelnn51` y el push completa en 2 segundos.
7. **Protección de Cola FIFO Local vs Detector de IID y Límite 256c de Telegram:**
   - *Causa auditada:* (1) Pegar varias keys con dígitos en el chat normal activaría `handleDirectIID` (falso `IID incompleto` o llamada errónea a Microsoft); (2) `/pedidos` colisionaba con regex `/pedido<codigo>` si no se excluía `'s'`; (3) `copy_text.text > 256` caracteres en lotes multi-key provocaba rechazo HTTP 400 en Telegram Bot API.
   - *Solución:* Interceptor `pendingStockUpload` con TTL en `bot.on('text')` antes de `handleDirectIID`, exclusión explícita de `'s'` y orden de registro tras `/pedidos`, consumo atómico en `db.transaction`, y división automática de botones `copy_text` si el bloque supera 256 caracteres.

### ❌ Caminos Técnicos Descartados (Prohibido reintentar)
- **Descartado:** Intentar pasar mensajes largos (>256 caracteres, como Pedido + Guía completa ~462-491c) dentro de botones `copy_text` de Telegram (Telegram API rechaza estrictamente con `400 BUTTON_COPY_TEXT_INVALID` por encima de 256c).
- **Descartado:** Enviar `<pre>` suelto sin cabecera para pedidos cortos `/pedido` (en mensajes cortos `<=256c` es mejor el botón nativo `copy_text`; para mensajes completos `>256c` como `/pedido21` y `/pedido24` con guía completa se usa Mensaje 1 Admin + Mensaje 2 `<pre><code class="language-WhatsApp">`).
- **Descartado:** Guardar stock local en memoria RAM o archivos JSON fuera de `/app/data` (se perdería en cada redeploy de Portainer).
- **Descartado:** Restricción `UNIQUE` en `key_stock.license_key` (impediría cargar varias filas de una misma key MAK multi-activación).
- **Descartado:** Permitir previsualizaciones de enlaces web en el bot (la tarjeta de cdkeysperu satura visualmente el chat).
- **Descartado:** Compartir un rate limiter de fallos de generación CID con el endpoint de consulta de balance (bloquea la lectura de saldos legítimos).
- **Descartado:** Asumir que cualquier respuesta sin `data.found === true` significa que la orden no existe (confunde rate limits 429 y errores de red con cuentas inexistentes).
- **Descartado:** Dejar que Git resuelva automáticamente la cuenta de GitHub en Windows si coexisten credenciales de varias organizaciones.

---

## 3. 📋 Tareas Pendientes y Roadmap Inmediato
- [ ] Monitoreo del OCR de IID con imágenes de baja resolución tomadas con flash en pantallas LED.
- [ ] Mantenimiento del token de Telegram para `@Cdkeysventas_bot`.

---

## 4. 🔗 Referencias Rápidas
- **Archivos críticos:** `frontend/public/index.html`, `frontend/bot.js`, `frontend/db.js`, `frontend/index.js`.
- **BD SQLite:** Tabla `order_credits` administrada con métodos seguros en `db.js`.
