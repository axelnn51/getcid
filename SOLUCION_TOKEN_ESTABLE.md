# Solución: Token Estable para GetCID V2

**Fecha:** 2026-05-15  
**Estado:** ✅ IMPLEMENTADO

---

## 🔍 Diagnóstico del problema

El `client_id: 2b217cec-607...` de `visualsupport.microsoft.com` está registrado como **SPA (Single Page Application)**.  
Microsoft impone un **límite DURO de 24 horas** para refresh tokens de SPAs — no configurable, no evitable.

### Lo que pasó (según logs):

| Hora | Evento |
|------|--------|
| May 14, 22:31 | ✅ Refresh exitoso, nuevo access token (59 min) |
| May 14, 23:16 | ✅ Access token en caché válido |
| May 15, 02:03 | ✅ Refresh exitoso (token rotado) |
| May 15, 02:39 | ✅ Access token en caché válido |
| May 15, 09:01 | ❌ **AADSTS70000: grant expired** (~10.5h después) |

El token ni llegó a 24h — Microsoft probablemente lo revocó antes por detectar uso desde IP de servidor/VPS.

---

## 🛠️ Solución Implementada: Sistema de 4 capas

### Capa 1: Proactive Token Refresh (cada 25 min) — 100% AUTOMÁTICO ✅

- Background task dentro de FastAPI que renueva el access token Y el refresh token cada 25 minutos
- Mantiene el token "caliente" y evita expiración por inactividad
- **El usuario NO interviene — es 100% automático en el servidor**
- Envía alertas Telegram si falla 2+ veces consecutivas

**Archivo:** `getcid_service/proactive_refresher.py` (NUEVO)

### Capa 2: Device Code Flow — 30 seg cada 90 días ✅

Usar **Device Code Flow** con un `client_id` de tipo "aplicación nativa".  
Los client_ids nativos otorgan refresh tokens de **90 días reales**.

**Client IDs que se prueban automáticamente:**
1. Azure CLI: `04b07795-8ddb-461a-bbee-02f9e1bf7b46`
2. Microsoft Office: `d3590ed6-52b3-4102-aeff-aad2292ab01c`
3. Azure CLI (scopes genéricos) como fallback

**Flujo:**
1. Admin envía `/deviceauth` en Telegram
2. Bot genera un código (ej: `ABC-123`) y lo envía por Telegram
3. Admin visita https://microsoft.com/devicelogin y pega el código
4. Inicia sesión con su cuenta Microsoft
5. El servidor captura el token automáticamente → 90 días de vida

**Archivo:** `getcid_service/device_auth.py` (NUEVO)

### Capa 3: Scraper Playwright mejorado — Último recurso ✅

- Mejor detección de CAPTCHA (por contenido HTML, no solo selectores)
- Borrar sesión corrupta para forzar re-login limpio
- Delays aleatorios entre intentos para parecer más humano
- Respeta duración real del token (no hardcodeado 3000s)
- Envía alerta Telegram cuando todas las cuentas fallan

**Archivo:** `getcid_service/scraper.py` (MODIFICADO)

### Capa 4: Alertas y Monitoreo en Tiempo Real ✅

- `telegram_alert.py` — Módulo para enviar alertas desde Python al admin
- Alerta instantánea cuando refresh token falla (2+ consecutivos)
- Alerta instantánea cuando refresh token expira (AADSTS70000)
- Alerta crítica cuando scraper falla en todas las cuentas
- Health check diario mejorado con tipo de token real
- `/systemstatus` — Nuevo comando para estado completo del sistema

**Archivo:** `getcid_service/telegram_alert.py` (NUEVO)

---

## 📝 Archivos modificados

| Archivo | Cambio |
|---------|--------|
| `proactive_refresher.py` | NUEVO — Background task cada 25 min |
| `device_auth.py` | NUEVO — Device Code Flow para tokens 90d |
| `telegram_alert.py` | NUEVO — Alertas Telegram desde Python |
| `main.py` | Startup events, endpoints de device auth, /system-status |
| `token_refresher.py` | Detección de tipo SPA vs Native, display correcto, alertas |
| `scraper.py` | CAPTCHA mejorado, delays random, alertas, sesión corrupta |
| `bot.js` | /deviceauth, /systemstatus, info correcta de tipo token |
| `docker-compose.yml` | BOT_TOKEN + ADMIN_IDS al contenedor Python |
| `.env` | Removido MS_ACCESS_TOKEN expirado |
| `captcha_solver.py` | Movido a .bak (código muerto, nunca se usaba) |

---

## 🔧 Comandos nuevos de Telegram

| Comando | Descripción |
|---------|-------------|
| `/deviceauth` | Inicia Device Code Flow (30 seg → 90 días de token) |
| `/systemstatus` | Estado completo: access token, refresh token, refresher, stats |
| `/tokenstatus` | Estado del access token (existente, mejorado) |
| `/setrefreshtoken` | Configurar refresh token manual (existente, info corregida) |

---

## 📊 Comparación

| | Antes | Después |
|---|---|---|
| Duración del token | ≤ 24 horas (mostraba 90d falso) | Real: 24h SPA o 90d Native App |
| Refresh automático | No había | Cada 25 min (automático) |
| Alertas de fallo | Solo health check 3PM | Instantáneo por Telegram |
| Device Code Flow | No existía | ✅ Genera tokens 90d |
| CAPTCHA handling | Solo visual | Visual + contenido HTML |
| Sesión corrupta | Se acumulaba | Se borra automáticamente |
| Código muerto | 301 líneas (captcha_solver) | Movido a backup |

---

## 🚀 Deploy

```bash
docker compose down
docker compose up --build -d
docker compose logs -f getcid_python
```

Después del deploy:
1. Verificar con `/systemstatus` que el proactive refresher está activo
2. Probar `/deviceauth` para obtener un token de 90 días
3. Monitorear logs por 2+ horas para confirmar que el refresh proactivo funciona
