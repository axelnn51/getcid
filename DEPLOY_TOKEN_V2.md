# GETCID Token Estable V2 — Instrucciones de Deploy

**Fecha:** 2026-05-15  
**Estado:** ✅ Código implementado, pendiente deploy

---

## 📋 Qué se hizo

Se implementó un sistema de 4 capas para resolver el problema del token que expiraba en ~10 horas (el client_id SPA tiene un límite duro de 24h de Microsoft).

### Archivos nuevos creados:
| Archivo | Qué hace |
|---------|----------|
| `getcid_service/proactive_refresher.py` | Renueva el token cada 25 min automáticamente (24/7, sin intervención) |
| `getcid_service/device_auth.py` | Device Code Flow: 30 seg de setup manual → 90 días de token |
| `getcid_service/telegram_alert.py` | Alertas instantáneas al admin cuando algo falla |

### Archivos modificados:
| Archivo | Cambio |
|---------|--------|
| `getcid_service/main.py` | Arranca proactive refresher al inicio + endpoints nuevos (`/api/device-auth-start`, `/api/system-status`) |
| `getcid_service/token_refresher.py` | Detecta tipo de token (SPA=24h vs Native=90d), muestra info REAL, alertas Telegram |
| `getcid_service/scraper.py` | Delays aleatorios (más humano), CAPTCHA por HTML, limpia sesiones corruptas, alerta si todo falla |
| `bot.js` | Nuevos comandos: `/deviceauth`, `/systemstatus`. Fix: ya no dice "90 días" para tokens SPA |
| `docker-compose.yml` | `BOT_TOKEN` + `ADMIN_IDS` pasados al contenedor Python (para alertas Telegram) |

### Limpieza:
- `captcha_solver.py` → renombrado a `.bak` (301 líneas de código muerto)
- `.env` → removido `MS_ACCESS_TOKEN` hardcodeado expirado
- `SOLUCION_TOKEN_ESTABLE.md` → actualizado con V2

---

## 🚀 Pasos para Deploy

### 1. Subir archivos al servidor
Sube todos los archivos modificados. Los nuevos archivos en `getcid_service/` son:
- `proactive_refresher.py`
- `device_auth.py`
- `telegram_alert.py`

### 2. Agregar variables de entorno en Portainer
El contenedor `getcid_python` ahora necesita 2 variables nuevas (ya están en `docker-compose.yml`):
```
BOT_TOKEN=8334632533:AAEMCDWK-4sMpmDSSquc5Afz6FRVZjrs6go
ADMIN_IDS=7233007906
```
*(Estas ya estaban en el contenedor Node, ahora también van al Python)*

### 3. Rebuild y restart
```bash
docker compose down
docker compose up --build -d
```

### 4. Verificar que funciona
```bash
# Ver logs del servicio Python (buscar "Proactive Token Refresher INICIADO")
docker compose logs -f getcid_python
```

En Telegram:
- `/systemstatus` → Verificar que el proactive refresher esté activo
- `/deviceauth` → Intentar obtener token de 90 días (opcional, puede que no funcione con la API de visualsupport)
- `/tokenstatus` → Ver estado del access token

### 5. Monitorear
- El proactive refresher debería hacer su primer refresh ~1 minuto después del arranque
- Después cada 25 minutos
- Si falla 2+ veces seguidas, recibirás alerta en Telegram
- El health check diario (3PM Perú) ahora muestra info del refresher

---

## 🆕 Comandos de Telegram nuevos

| Comando | Descripción |
|---------|-------------|
| `/deviceauth` | Inicia Device Code Flow → te da un código, lo pegas en microsoft.com/devicelogin, y obtienes token de 90 días |
| `/systemstatus` | Estado completo: access token, refresh token, proactive refresher, estadísticas |

---

## ⚠️ Notas importantes

1. **El proactive refresh (Capa 1) es automático al 100%**. No tienes que hacer nada. Se lanza solo cuando el servidor arranca.

2. **El Device Code Flow (Capa 2) tiene ~40-50% de funcionar** con la API de visualsupport. Si el client_id nativo es rechazado, no pasa nada — la Capa 1 sigue funcionando.

3. **Si el token muere**, ahora recibirás alerta instantánea por Telegram (antes solo te enterabas en el health check de las 3PM o cuando un cliente fallaba).

4. **El scraper ahora borra sesiones corruptas** automáticamente cuando detecta CAPTCHA, en vez de acumularlas.
