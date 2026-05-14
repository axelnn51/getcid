# 🚀 GetCID - Instrucciones de Deploy Final (v2 - Con Xvfb)
**Fecha:** 14 de Mayo 2026  
**Estado:** Código corregido con anti-detección Xvfb, listo para desplegar.

---

## 🧠 ¿Qué cambió respecto a la versión anterior?

**Antes:** Chrome corría en modo `headless=True` en el servidor → Microsoft lo detectaba como bot → CAPTCHA.

**Ahora:** Chrome corre en modo `headless=False` (como un navegador real) renderizado a una **pantalla virtual (Xvfb)** en la memoria RAM del servidor. Microsoft NO puede distinguir esto de un usuario real sentado frente a su PC.

## ✅ Lo que ya funciona
- **Local (NUEVOGETCID):** Probado y funcionando al 100%.
- **Sesión de Microsoft:** Guardada en `getcid_service/states/state_axelnn52_outlook_com.json`.
- **Anti-detección:** Xvfb + playwright-stealth + modo headed = navegador invisible para Microsoft.

## ❌ Lo que falta hacer (3 pasos)

### Paso 1: Subir cambios a GitHub
```bash
cd C:\Users\axeln\OneDrive\Desktop\GETCID
git add .
git commit -m "Fix: Xvfb anti-captcha + headed mode en servidor"
git push
```

### Paso 2: Borrar imagen vieja en Portainer
1. Ve a Portainer → **Stacks** → `getcid` → botón rojo **Stop this stack**
2. Ve a **Images** en el menú izquierdo
3. Busca `getcid-getcid_python:latest` (la de ~3GB)
4. Marca la casilla y dale **Remove** (o Force Remove si pide)
5. Si también ves `getcid-getcid:latest`, bórrala también (para que Node.js también se reconstruya limpio)

### Paso 3: Redesplegar
1. Ve a **Stacks** → `getcid`
2. Dale al botón **Pull and redeploy**
3. Espera ~3-5 minutos (la imagen ahora incluye Xvfb, puede tardar un poco más)
4. Ve a **Containers** → `getcid_python` → Quick Actions → primer icono (Logs)
5. Verifica que diga: `Uvicorn running on http://0.0.0.0:8000`
6. Prueba enviando un IID por Telegram

---

## 📋 Resumen de TODOS los cambios

| Archivo | Cambio | Razón |
|---|---|---|
| `getcid_service/Dockerfile` | `apt install xvfb` + `CMD xvfb-run ...` | Pantalla virtual para que Chrome corra como navegador real |
| `getcid_service/scraper.py` | `headless=False` siempre + `IS_SERVER` env var | Chrome nunca corre headless → Microsoft no detecta bot |
| `docker-compose.yml` | `IS_SERVER=true` + sin volume mount de states | Señala modo servidor + sesión baked en imagen |
| `getcid_service/main.py` | Try/catch con traceback | Errores detallados en vez de crash 500 |
| `.gitignore` | Permite `states/` | Sesión se sube a GitHub (repo privado) |

## 🔑 Cómo funciona el flujo anti-CAPTCHA

```
[Docker Container]
    │
    ├── Xvfb crea pantalla virtual 1280x720 en RAM
    │
    ├── Chrome se lanza en modo HEADED (como navegador normal)
    │   └── Renderiza a la pantalla virtual (invisible pero "real")
    │
    ├── playwright-stealth oculta huellas de automatización
    │
    ├── Cookies pre-guardadas se cargan → Microsoft reconoce sesión existente
    │
    └── Token Bearer se captura de las peticiones → API devuelve CID
```

**¿Por qué funciona?** Microsoft detecta bots checando:
1. ❌ `navigator.webdriver = true` → **stealth lo oculta**
2. ❌ Chrome en modo headless → **Xvfb lo evita (siempre headed)**
3. ❌ No hay interacción humana previa → **las cookies demuestran sesión anterior**

## ⚠️ Si la sesión expira en el futuro
1. Ve a tu PC local, abre consola en `NUEVOGETCID`
2. Ejecuta: `.\venv\Scripts\python.exe -m uvicorn main:app --port 8000`
3. Ve a `localhost:8000`, pon un IID y dale al botón (se abrirá Chrome para login)
4. Inicia sesión manualmente → la sesión se guarda en `states/`
5. Copia el archivo: `cp states\state_*.json ..\GETCID\getcid_service\states\`
6. Haz git add, commit, push desde GETCID
7. En Portainer: borra imagen python vieja → Pull and redeploy

## 🌐 Variables de entorno en Portainer
```
BOT_TOKEN=8334632533:AAEMCDWK-4sMpmDSSquc5Afz6FRVZjrs6go
ADMIN_IDS=7233007906
ADMIN_PASSWORD=cdkeysperu2024
WC_URL=https://cdkeysperu.com
WC_CONSUMER_KEY=ck_38f5e2320bb706bfd56dd6bd83d7cfb19d5954f4
WC_CONSUMER_SECRET=cs_cdff0c9f3f19522cca3ef91c5989a9020b82b6c0
MS_ACCOUNTS=axelnn52@outlook.com:TU_PASSWORD_AQUI
```
