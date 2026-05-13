# 🚀 Guía de Deploy — Cloudflare Worker Proxy para GETCID

## ¿Por qué es necesario?

Microsoft bloquea peticiones directas a su API de activación (`visualsupport.microsoft.com`) desde IPs de VPS y PCs.  
Servicios como Keys4Us funcionan porque usan **Cloudflare Workers** como proxy (las IPs de edge de Cloudflare sí son aceptadas por Microsoft).

**Arquitectura:**
```
Tu Bot/Web (VPS) → Cloudflare Worker (proxy) → Microsoft API → CID ✅
```

---

## Paso 1: Instalar Wrangler (CLI de Cloudflare)

```bash
npm install -g wrangler
```

---

## Paso 2: Iniciar sesión en Cloudflare

```bash
wrangler login
```

Se abrirá el navegador para autorizar. Si no tienes cuenta, créala gratis en [dash.cloudflare.com](https://dash.cloudflare.com).

---

## Paso 3: Desplegar el Worker

Desde la carpeta del proyecto GETCID:

```bash
cd C:\Users\axeln\OneDrive\Desktop\GETCID
wrangler deploy
```

Esto desplegará `worker_proxy.js` y te dará una URL como:
```
https://getcid-proxy.tu-usuario.workers.dev
```

**Copia esa URL**, la necesitas para el siguiente paso.

---

## Paso 4: (Opcional) Configurar API Key de seguridad

Para que solo tu bot pueda usar el worker:

```bash
wrangler secret put WORKER_API_KEY
```

Escribe una clave secreta (ej: `mi_clave_super_secreta_2024`).

---

## Paso 5: Configurar tu .env

Edita el archivo `.env` del proyecto y llena estas variables:

```env
WORKER_PROXY_URL=https://getcid-proxy.tu-usuario.workers.dev
WORKER_API_KEY=mi_clave_super_secreta_2024
```

> Si no pusiste API Key en el paso 4, deja `WORKER_API_KEY` vacío.

---

## Paso 6: Rebuild y deploy del bot

### Opción A: Docker Compose (local o VPS)
```bash
docker-compose up -d --build
```

### Opción B: Portainer
1. Ve a tu stack de GETCID en Portainer
2. Actualiza las variables de entorno con `WORKER_PROXY_URL` y `WORKER_API_KEY`
3. Haz pull de la imagen y redeploy

---

## Verificación

1. Envía un IID al bot de Telegram
2. Debería devolver el CID correctamente ✅
3. En los logs del container verás: `[CID] Usando Worker proxy: https://...`

Si el proxy falla, el sistema automáticamente intenta la conexión directa como fallback.

---

## Costos

Cloudflare Workers tiene un **tier gratuito** de 100,000 requests/día — más que suficiente para GETCID.

---

## Resumen de cambios en el código (ya pusheado a GitHub)

| Archivo | Cambio |
|---|---|
| `worker_proxy.js` | **NUEVO** — El worker de Cloudflare |
| `wrangler.toml` | **NUEVO** — Config del worker |
| `cid_helper.js` | Usa proxy como método principal, directo como fallback |
| `.env` | Nuevas vars: `WORKER_PROXY_URL`, `WORKER_API_KEY` |
| `docker-compose.yml` | Pasa las nuevas vars al container |
