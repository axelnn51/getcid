# 📋 GetCID System — Documentación Final
**Fecha:** 14 de Mayo de 2026, 01:08 AM (hora Perú)  
**Autor:** Antigravity AI  
**Versión:** 3.0 — Sistema de Créditos por Pedido + Auto-renovación

---

## 🏗️ Arquitectura del Sistema

```
┌──────────────────┐     ┌────────────────────┐     ┌─────────────────┐
│  Telegram Bot     │────▶│  Node.js (Express)  │────▶│  Python (FastAPI)│
│  (Telegraf)       │     │  Puerto 3000        │     │  Puerto 8000     │
│                   │     │  bot.js + index.js   │     │  main.py         │
└──────────────────┘     └────────────────────┘     └─────────────────┘
         │                       │                          │
         │               ┌──────┴──────┐             ┌─────┴──────┐
         │               │  SQLite DB  │             │  Token      │
         │               │  getcid.db  │             │  Refresher  │
         │               └─────────────┘             └────────────┘
         │                                                  │
    ┌────┴─────────┐                               ┌────────┴─────────┐
    │ Web Portal   │                               │  Microsoft API   │
    │ getcid.      │                               │  visualsupport.  │
    │ cdkeysperu   │                               │  microsoft.com   │
    └──────────────┘                               └──────────────────┘
```

---

## 🔑 Sistema de Autenticación (Zero-CAPTCHA)

### Flujo de Refresh Tokens
1. **Login local (cada ~90 días):** El admin ejecuta `scraper.py` en su PC Windows
2. **Captura automática:** El scraper intercepta el Refresh Token de la respuesta OAuth
3. **Envío al servidor:** Via Telegram `/setrefreshtoken {json}` o `/api/setrefreshtoken`
4. **Auto-renovación:** El servidor usa el Refresh Token para obtener Access Tokens cada hora
5. **Cadena infinita:** Cada refresh renueva AMBOS tokens → funciona indefinidamente

### Archivos clave
| Archivo | Función |
|---------|---------|
| `getcid_service/token_refresher.py` | Lógica de renovación OAuth2 |
| `getcid_service/scraper.py` | Captura tokens via Playwright (servidor) |
| `NUEVOGETCID/scraper.py` | Captura tokens via Playwright (local) |
| `/app/persist/ms_refresh_token.json` | Refresh token persistente (Docker volume) |
| `ms_token.json` | Access token (efímero, se regenera) |

### Jerarquía de autenticación (scraper.py del servidor)
```
1. ¿Hay access token en caché y vigente? → Usar
2. ¿Hay refresh token? → Renovar access token (sin navegador)
3. Último recurso: Playwright headless (siempre falla con CAPTCHA en datacenter)
```

---

## 💰 Sistema de Créditos (v3.0)

### Reglas
- **1 licencia comprada = 1 crédito**
- Un pedido con 3 licencias = 3 créditos
- Créditos asociados a **email** Y **número de pedido**

### Tabla `order_credits`
```sql
CREATE TABLE order_credits (
    order_id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    total_credits INTEGER NOT NULL,  -- licencias del pedido
    used_credits INTEGER DEFAULT 0,  -- cuántos se han consumido
    synced_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Lógica de consulta
| Método | Qué muestra | Consumo |
|--------|-------------|---------|
| **Nro de pedido** | Créditos de ESE pedido | Descuenta de ese pedido |
| **Email** | Suma de créditos de TODOS los pedidos | Descuenta del pedido más antiguo |

### Ejemplo
```
Pepito (pepito@correo.com) - Pedido #10782: 2 Windows + 1 Office = 3 créditos

→ Usa pedido #10782: ve "3 CIDs" → usa 1 → "2 CIDs"
→ Usa email: ve "2 CIDs" (suma global) → usa 1 → "1 CID"
→ Ambos comparten el mismo pool real
```

### Funciones db.js
```javascript
syncOrderCredits(orderId, email, totalLicenses) // Registra pedido
getOrderBalance(orderId)     // → { total, used, remaining }
getEmailBalance(email)       // → remaining total
consumeOrderCredit(orderId)  // Consume 1 de ese pedido
consumeEmailCredit(email)    // Consume 1 del pedido más antiguo
```

---

## 🤖 Comandos del Bot de Telegram

### Comandos de usuario
| Comando | Función |
|---------|---------|
| `/start` | Registro + verificar balance |
| Enviar IID como texto | Obtener CID directamente |
| Enviar foto | OCR → extraer IID → obtener CID |

### Comandos de admin
| Comando | Función |
|---------|---------|
| `/addcredits <tg_id> <n>` | Agregar créditos manuales (Telegram) |
| `/stats` | Estadísticas globales |
| `/tokenstatus` | Estado del access token actual |
| `/settoken <token>` | Inyectar access token manualmente |
| `/setrefreshtoken <json>` | Configurar refresh token (90 días) |

---

## 🌐 API Endpoints

### Python (FastAPI - Puerto 8000)
| Endpoint | Método | Función |
|----------|--------|---------|
| `/api/getcid` | POST | Obtener CID dado un IID |
| `/api/settoken` | POST | Inyectar access token |
| `/api/token-status` | GET | Estado del access token |
| `/api/setrefreshtoken` | POST | Configurar refresh token |
| `/api/refreshtoken-status` | GET | Estado del refresh token |
| `/api/health` | GET | Health check |

### Node.js (Express - Puerto 3000)
| Endpoint | Método | Función |
|----------|--------|---------|
| `/api/portal/getcid` | POST | Portal web con créditos |
| `/api/check-balance` | GET | Verificar balance (email o pedido) |
| `/api/process-image` | POST | OCR sin créditos |
| `/api/admin/users` | GET | Lista usuarios (admin) |
| `/api/admin/stats` | GET | Estadísticas (admin) |
| `/api/admin/add-credits` | POST | Agregar créditos (admin) |

---

## 📊 Monitoreo Automático

### Health Check diario (3:00 PM hora Perú)
El bot envía al admin cada día:
- Estado del Access Token
- Estado del Refresh Token (días restantes de 90)
- CIDs generados hoy
- CIDs totales
- Uptime del servidor

### Notificaciones Web → Telegram
Cada vez que alguien usa `getcid.cdkeysperu.com`:
```
🌐 CID desde Web
👤 pepito@correo.com
📝 IID: 123456...
🔑 CID: 394960-045759-...
💰 Balance: Global: 4
```

---

## 🐳 Docker Compose

```yaml
services:
  getcid:
    build: .
    ports: ["3000:3000"]
    volumes: [./data:/app/data]         # SQLite DB persistente
    depends_on: [getcid_python]
    
  getcid_python:
    build: ./getcid_service
    volumes: [./python_data:/app/persist]  # Refresh token persistente
```

### Volúmenes persistentes
| Volumen | Contenido |
|---------|-----------|
| `./data/` | `getcid.db` (base de datos SQLite) |
| `./python_data/` | `ms_refresh_token.json` (token de 90 días) |

---

## 📁 Estructura de Archivos

```
GETCID/
├── bot.js                    # Bot Telegram (comandos, CID, notificaciones)
├── index.js                  # Express server (web portal, API)
├── cid_helper.js             # Comunicación con Python service
├── db.js                     # SQLite DB (usuarios, créditos, transacciones)
├── woocommerce.js            # Integración WooCommerce API
├── ocr.js                    # Motor OCR (Tesseract)
├── docker-compose.yml
├── Dockerfile
├── .env                      # Variables de entorno
├── data/
│   └── getcid.db             # Base de datos
├── public/                   # Frontend web
├── getcid_service/
│   ├── main.py               # FastAPI server
│   ├── scraper.py            # Playwright login (fallback)
│   ├── token_refresher.py    # OAuth2 refresh token logic
│   ├── captcha_solver.py     # CapSolver (reserva futura)
│   └── Dockerfile
└── python_data/              # Volumen Docker persistente
    └── ms_refresh_token.json
```

```
NUEVOGETCID/                  # Copia local (PC Windows del admin)
├── scraper.py                # Login local + captura refresh token
├── main.py                   # API local para pruebas
├── auto_refresh.py           # Script automático (legacy)
├── ms_refresh_token.json     # Refresh token capturado localmente
└── .env
```

---

## 🛠️ Mantenimiento

### Cada 90 días (renovar refresh token)
```bash
# En tu PC Windows:
cd C:\Users\axeln\OneDrive\Desktop\NUEVOGETCID
.\venv\Scripts\python.exe scraper.py
# → Login en Chrome → esperar captura → copiar ms_refresh_token.json

# En Telegram:
/setrefreshtoken {contenido_del_json}
```

### Tarea programada `GetCID_TokenRefresh` (DESACTIVADA)
Existía una tarea de Windows que ejecutaba `NUEVOGETCID\refresh_token.bat` cada 50 minutos.  
Fue **desactivada el 14/05/2026** porque causaba que al reiniciar el PC se abriera un CMD con códigos y la página de Microsoft.  
Ya no es necesaria porque el servidor Docker renueva tokens automáticamente.

```powershell
# Para reactivarla (solo si se necesita):
schtasks /Change /tn "GetCID_TokenRefresh" /Enable

# Para desactivarla:
schtasks /Change /tn "GetCID_TokenRefresh" /Disable

# Para eliminarla definitivamente:
schtasks /Delete /tn "GetCID_TokenRefresh" /F
```

### Si el bot falla
1. Verificar logs en Portainer
2. `/tokenstatus` en Telegram
3. Si token expirado: `/settoken <token>` (temporal) o renovar refresh token

### Si necesitas redesplegar
```bash
cd C:\Users\axeln\OneDrive\Desktop\GETCID
git add . && git commit -m "descripcion" && git push
# Portainer: borrar imágenes → Pull and redeploy
# Re-enviar /setrefreshtoken si es la primera vez con el volumen
```

---

## 🔧 Variables de Entorno (.env)

```env
BOT_TOKEN=tu_token_telegram
ADMIN_IDS=tu_telegram_id
ADMIN_PASSWORD=tu_password_admin_web
WC_URL=https://cdkeysperu.com
WC_CONSUMER_KEY=ck_xxx
WC_CONSUMER_SECRET=cs_xxx
MS_EMAIL=axelnn52@outlook.com
MS_PASSWORD=tu_password
GETCID_SERVICE_URL=http://getcid_python:8000
```

---

## 🐛 Troubleshooting

| Problema | Causa | Solución |
|----------|-------|----------|
| `fetch failed` en Telegram | Python service no arrancó | Verificar logs de `getcid_python` en Portainer |
| `No se pudo obtener token de Microsoft` | Access + refresh tokens expirados | Enviar `/setrefreshtoken` |
| `CAPTCHA` en servidor | IP datacenter detectada | Normal, usar refresh tokens |
| `0 CIDs disponibles` | Pedido no existe en WooCommerce o ya consumido | Verificar en WooCommerce |
| Doble guion en CID (`--`) | **CORREGIDO v3.0** — formatCID limpia dígitos primero | — |
| Token perdido tras redeploy | **CORREGIDO v3.0** — volumen persistente | — |

---

> **Estado:** Sistema operativo 24/7. Próxima acción manual: ~8 de Agosto 2026 (renovar refresh token).
