# 📍 ACTIVE_CONTEXT: GETCID Final (Portal y Backend Activación Licencias)
> Última Actualización: 2026-11-01 | Versión: Estable v3.9 | Estado: ✅ PUSHEADO A GITHUB Y OPERATIVO EN PRODUCCIÓN

## 1. 🚦 Estado Actual Inmediato
- **Servicio Web:** `getcid.cdkeysperu.com` (Frontend simétrico de 3 columnas alineado a 525px).
- **Backend / DB:** Node.js + Express con base de datos SQLite (`order_credits`).
- **Bot de Telegram:** Generador de pedidos manuales (`/pedido <créditos> [producto]`, `/pedidos`).
- **Formato Oficial 1-Click WhatsApp:**
  - Bloque suave en `<code>` nativo de Telegram (sin la caja negra `<pre>`).
  - Sin previsualización de enlaces invasiva (`disable_web_page_preview: true`).
  - Copia exactamente en 1 solo clic:
    ```text
    📦 N° de Pedido: CDK-XXXXX
    💰 Créditos CID: X
    🌐 Portal Web CID: https://getcid.cdkeysperu.com/
    🛒 Producto: 
    ```
- **Control de Tasa y Balance:** Rate limiters desacoplados (balance permisivo 60 req/min vs portal 30 fallos/10 min). Normalización integral de códigos de pedido (`CDK-XXXXX`, `#CDK-XXXXX`, dígitos directos).
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

### ❌ Caminos Técnicos Descartados (Prohibido reintentar)
- **Descartado:** Usar bloque `<pre>` aislado (genera recuadro negro poco estético en Telegram Desktop con barra superior de depuración).
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
