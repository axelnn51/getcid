# 🚀 GETCID 2.0: Arquitectura Definitiva y Plan de Implementación (Zero-Cost & Zero-Browser)

Este documento sirve como la bitácora histórica y el mapa arquitectónico para la reconstrucción total del sistema **GETCID**. El objetivo supremo es crear un sistema 100% infalible, autónomo, que cueste $0 (0 soles en APIs externas o servicios de CAPTCHA), y que sea inmune a los sistemas anti-bots de Microsoft.

---

## 📖 Parte 1: Lecciones Aprendidas (Éxitos y Fracasos de la V1)

Para construir un sistema infalible, debemos entender por qué falló el anterior y qué cosas sí funcionaron.

### ❌ Los Fracasos y Cuellos de Botella (Lo que eliminaremos)
1. **El Motor Gráfico en Linux (Shadow Ban):** Usar `Playwright` o Selenium en un contenedor Docker con Ubuntu Server es el mayor error a largo plazo. Microsoft (Arkose Labs) detecta la falta de GPU, firmas de Canvas virtuales y emulación de Xvfb. Castigo: Pantallas de carga infinitas ("Loading... Please wait").
2. **Dependencia de la Interfaz Visual (UI Changes):** El login de Microsoft muta constantemente (ej. la caja del código pasó de llamarse `otc` a `iCode`). Mapear selectores web (`page.locator`) es una carrera armamentística que siempre perderemos.
3. **El Costo Oculto de los CAPTCHAs:** Resolver CAPTCHAs con IA (Gemini) es brillante, pero agota cuotas gratuitas rápidamente cuando hay bucles, paralizando la operación diaria.

### ✅ Los Éxitos (Lo que mantendremos)
1. **La Criptografía DPoP (`core.py`):** Logramos decodificar y replicar a la perfección el sistema de firmas criptográficas (ES256) de Microsoft. Esto es el núcleo que permite que los tokens funcionen.
2. **Renovación Silenciosa (`token_refresher.py`):** Descubrimos que, inyectando `"token_type": "pop"`, podemos renovar tokens en segundo plano infinitamente sin usar el navegador.
3. **Lectura IMAP Silenciosa:** El módulo de extracción de códigos desde Gmail funciona como un reloj suizo.

---

## 🏗️ Parte 2: Arquitectura del Sistema Infalible (GETCID 2.0)

El nuevo sistema abandonará por completo la automatización de navegadores web en el servidor. Se basará en la filosofía **"Zero-Browser"** y **"Bring Your Own Token (BYOT)"**.

### Pilar A: El "Extractor Local" (El Caballo de Troya)
Dado que los CAPTCHAs de Arkose Labs atacan a los servidores Linux, no intentaremos resolverlos allí.
* Se creará un script súper liviano (o ejecutable) para tu **PC Personal (Windows)**.
* Tú correrás este script en tu casa. El script abrirá el Chrome *real* de tu computadora. 
* Como tu computadora es 100% real y tu IP es residencial limpia, Microsoft no te pondrá trabas ni CAPTCHAs imposibles (y si los pone, los resuelves tú en 3 segundos).
* Una vez que inicias sesión, este "Extractor" roba silenciosamente tu `refresh_token` y tu firma primaria de DPoP, y los transmite instantáneamente por red (o lo copias/pegas) a tu servidor Ubuntu.

### Pilar B: El "Inmortal" en Ubuntu (El Servidor)
El servidor Docker ya no tendrá 1.5GB de binarios de Chromium. Será una aplicación Python ultra ligera (apenas 50MB).
* Recibirá el `refresh_token` maestro de tu Extractor Local.
* Utilizará el **Motor DPoP** nativo para falsificar las firmas criptográficas.
* Lanzará un demonio (background task) que renovará el token silenciosamente a nivel HTTP (Capa 7) cada 12 horas.
* Expondrá el API FastAPI en el puerto 8000 para que envíes PIDs, y devolverá los CIDs instantáneamente.

**Beneficios Inmediatos:**
1. **Costo $0 Garantizado:** Al no enfrentar a Arkose Labs en el servidor, no necesitas pagar proxies, ni CapSolver, ni agotar las APIs de Gemini.
2. **Consumo 95% menor:** Sin Playwright, el servidor casero no sufrirá cuellos de botella de CPU/RAM.
3. **Infalible:** Microsoft no bloquea peticiones HTTP puras si llevan la firma DPoP correcta y provienen de un Token Maestro sano.

---

## 🛠️ Parte 3: Plan de Ejecución Paso a Paso

Construiremos este proyecto en la carpeta `GETCID 2.0` de forma limpia y modular:

### Paso 1: El Núcleo Criptográfico y la API (Backend)
- Migrar y limpiar `core.py` (Solo lógica de JWT y DPoP).
- Crear `auth_http.py` (Lógica de renovación pura usando la librería `httpx`).
- Montar `main.py` (FastAPI con endpoints `/check_pid` y `/status`).

### Paso 2: El Extractor Local (Frontend/Auth)
- Crear el script `local_extractor.py`.
- Utilizar `undetected_chromedriver` o Playwright headed **exclusivamente para PC local**.
- Empaquetar la lógica para que, una vez capture el `sessionStorage`, exporte un archivo `session_master.json`.

### Paso 3: El Puente (Integración)
- Crear el sistema de sincronización: El servidor leerá el archivo `session_master.json` para tomar control.
- Programar el demonio que renueva el token cada cierto tiempo usando lógica asíncrona pura.

### Paso 4: Pruebas y Despliegue
- Escribir un nuevo `docker-compose.yml` ultra ligero basado en Alpine/Slim.
- Desplegar en tu servidor Portainer.

---

> *"Un sistema verdaderamente inteligente no es el que pelea y gana todas las batallas contra el enemigo (Microsoft), sino el que no necesita pelear porque es invisible."*
