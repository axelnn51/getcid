# 📋 GetCID System — Documentación Maestra (V3.0)

**Fecha de Actualización:** Mayo 2026  
**Descripción:** Este documento consolida toda la información arquitectónica, de despliegue, soluciones y guías del proyecto GetCID en un solo lugar.

---

## 1. 🏗️ Arquitectura del Sistema

```text
┌──────────────────┐     ┌────────────────────┐     ┌─────────────────┐
│  Telegram Bot    │────▶│  Node.js (Express) │────▶│  Python (FastAPI)│
│  (Telegraf)      │     │  Puerto 3000       │     │  Puerto 8000     │
│                  │     │  bot.js + index.js │     │  main.py         │
└──────────────────┘     └────────────────────┘     └─────────────────┘
         │                       │                          │
         │               ┌──────┴──────┐             ┌─────┴──────┐
         │               │  SQLite DB  │             │  Token     │
         │               │  getcid.db  │             │  Refresher │
         │               └─────────────┘             └────────────┘
         │                                                  │
    ┌────┴─────────┐                               ┌────────┴─────────┐
    │ Web Portal   │                               │  Microsoft API   │
    │ getcid.      │                               │  visualsupport.  │
    │ cdkeysperu   │                               │  microsoft.com   │
    └──────────────┘                               └──────────────────┘
```

El sistema consta de dos contenedores principales (`getcid` en Node.js y `getcid_python` en Python) que interactúan para proporcionar interfaces (web/Telegram), manejar la base de datos de créditos y generar los CID usando un token de Microsoft estable.

---

## 2. 🔑 Sistema de Autenticación Estable (Zero-CAPTCHA)

El principal problema resuelto en la versión V2/V3 fue la caducidad abrupta del token a las 24 horas (debido a las restricciones de las SPA de Microsoft) y los bloqueos por CAPTCHA. Esto se resolvió mediante un **Sistema de 4 Capas**:

### Capa 1: Proactive Token Refresh (100% Automático)
- Tarea en segundo plano (`proactive_refresher.py`) que renueva el *access token* Y el *refresh token* cada 25 minutos.
- Evita la expiración por inactividad. Si falla 2 veces seguidas, envía alerta por Telegram.

### Capa 2: Device Code Flow (Tokens de 90 Días)
- Las cuentas de Microsoft "Profesionales o Educativas" (Microsoft Entra) permiten obtener tokens nativos válidos por 90 días completos usando el flujo `Device Code`.
- **Uso:** El comando `/deviceauth` del bot inicia el flujo, te entrega un código que pegas en `microsoft.com/devicelogin` para aprobar el inicio de sesión.

### Capa 3: Scraper Playwright con Xvfb (Último recurso)
- Para evitar que Microsoft detecte el contenedor como "bot" (lo cual causa CAPTCHAs imposibles), Playwright se ejecuta en modo `headed` renderizando su salida visual en una pantalla virtual de memoria RAM usando **Xvfb**.
- Adicionalmente, cuenta con delays aleatorios y limpieza automática de sesiones corruptas.

### Capa 4: CAPTCHA Solver Híbrido (IA + Manual)
- **Plan A (Gemini AI Vision):** En caso de que aparezca un CAPTCHA, el sistema usa `ai_solver.py` enviando una captura a **Gemini 1.5 Pro** para que cuente cuántos clics se requieren para alinear la imagen 3D (Arkose Labs). El script ejecuta los clics automáticamente.
- **Plan B (Humano vía Telegram):** Como respaldo absoluto si la IA falla o se queda sin créditos, se envían botones interactivos al admin por Telegram para resolver el puzzle remotamente.

---

## 3. 💳 Sistema de Créditos por Pedido (WooCommerce)

El bot de Telegram y la interfaz web validan licencias usando una base de datos local `getcid.db` que sincroniza pedidos con WooCommerce (`cdkeysperu.com`).

- **Regla:** 1 licencia comprada = 1 crédito de CID.
- **Consultas:** El balance puede verificarse por Número de Pedido o por Email (el email suma todos los pedidos y gasta del más antiguo).
- Los créditos solo se consumen cuando un IID se procesa exitosamente en un CID.
- El reconocimiento visual (OCR de fotos enviadas por Telegram) se procesa a través de `tesseract.js` (cargando `eng.traineddata`) sin gastar créditos hasta que el IID se resuelve en Microsoft.

---

## 4. 🚀 Guía de Deploy y Mantenimiento

### Actualización del Proyecto
1. Confirma tus cambios al repositorio.
2. En Portainer > Stacks > `getcid` > Presiona **Pull and redeploy**.
3. Si cambiaste dependencias del sistema operativo (ej. Xvfb en el `Dockerfile`), es recomendable borrar las imágenes viejas de Docker en Portainer y hacer un rebuild forzado.

### Variables de Entorno (.env)
Las variables principales necesarias para el funcionamiento del servidor:
```env
BOT_TOKEN=8334632533:AAEMCDWK...
ADMIN_IDS=7233007906
ADMIN_PASSWORD=tu_password_web
WC_URL=https://cdkeysperu.com
WC_CONSUMER_KEY=ck_xxx
WC_CONSUMER_SECRET=cs_xxx
MS_ACCOUNTS=axelnn52@outlook.com:TU_PASSWORD
NOCAPTCHAAI_API_KEY=xxx
CAPSOLVER_API_KEY=CAP-xxx
AI_SOLVER_ENABLED=true
GEMINI_API_KEY=xxx
```

### Crear una cuenta Educativa/Profesional Gratuita
Para disfrutar de la Capa 2 (Tokens de 90 días), puedes crear un Tenant de Microsoft Entra gratis (sin tarjeta) en [entra.microsoft.com](https://entra.microsoft.com/):
1. Crea un nuevo Inquilino (Tenant).
2. Crea un usuario `admin@...onmicrosoft.com`.
3. Inicia sesión en Telegram con `/deviceauth` usando esta nueva cuenta. Esto garantiza 90 días libres de bloqueos de sesión o CAPTCHAs para el bot. (Nota: Si ya posees licencias A3/Pro Plus, tu cuenta ya cuenta como educativa).

---

## 5. 🤖 Comandos Principales de Telegram

| Comando | Función |
|---------|---------|
| `/start` | Mensaje de bienvenida y registro. |
| `/deviceauth` | (Admin) Inicia flujo para Token de 90 días. |
| `/systemstatus` | (Admin) Estado general de los tokens, IA y APIs. |
| `/tokenstatus` | (Admin) Verifica el tiempo restante del access token activo. |
| `/settoken` | (Admin) Sobrescribir el access token de forma manual. |
| `/setrefreshtoken` | (Admin) Inyectar un JSON de un nuevo refresh token. |
| `/stats` | (Admin) Estadísticas globales de CIDs. |
| `/addcredits` | (Admin) Agregar créditos manuales a un usuario de Telegram. |

> **Nota:** La antigua tarea programada de Windows (`GetCID_TokenRefresh`) está desactivada por ser redundante, ya que todo el sistema de renovación ahora corre internamente en el contenedor Linux usando FastAPI.
