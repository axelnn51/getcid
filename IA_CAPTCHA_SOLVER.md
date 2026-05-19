# 🤖 Plan de Integración: Resolución Autónoma de CAPTCHA con IA (Vision)

Actualmente, el sistema `remote_renovar.py` pausa su ejecución al encontrar el CAPTCHA de Arkose Labs (el tren y las flechas) y delega la resolución a un humano mediante la API de Telegram. 

Para lograr el **100% de autonomía (Zero-Touch)**, podemos integrar un modelo de Inteligencia Artificial Multimodal (Vision) como **Gemini 1.5 Pro** o **GPT-4o**.

A continuación se detalla la lógica perfecta y los pasos exactos para implementarlo.

---

## 1. Arquitectura del Flujo Autónomo

1. **Detección:** Playwright detecta el CAPTCHA (ya implementado).
2. **Captura:** Playwright toma un screenshot del iframe del puzzle completo y lo guarda en memoria (`captcha.png`).
3. **Análisis de IA:** En lugar de enviarlo a Telegram, el script hace un POST a la API de la IA (ej. OpenAI o Google Gemini) enviando la imagen codificada en Base64.
4. **Prompting Estratégico:** Se le da una instrucción precisa a la IA para que devuelva **únicamente un número**.
5. **Acción:** El script lee el número, hace el bucle de N clics a la derecha, y presiona "Submit".
6. **Fallback de Seguridad:** Si la IA se equivoca y sale "Try Again", o si hay un error de conexión, el sistema revierte automáticamente al método de Telegram para que el humano lo salve.

---

## 2. El Prompt Perfecto (Prompt Engineering)

Los modelos de visión actuales son increíblemente buenos para entender orientación. El prompt (instrucciones) a enviar junto a la imagen debe ser estricto para evitar que la IA "hable de más":

> *"Eres un experto solucionando puzzles lógicos. En la imagen adjunta hay un puzzle de 2 partes. A la izquierda hay un ícono de referencia mostrando una dirección específica. A la derecha hay un tren sobre unas vías en una imagen 3D.*
> *Tu tarea es determinar cuántos 'clics' en el botón de flecha derecha se necesitan para rotar la imagen del tren y que coincida EXACTAMENTE con la orientación y el ángulo de la imagen de referencia de la izquierda.*
> *Sabiendo que cada clic a la flecha rota la imagen una cantidad fija, dime el número exacto de clics.*
> *IMPORTANTE: Tu respuesta debe ser ÚNICAMENTE el número entero (ejemplo: 3). No escribas ninguna otra palabra, solo el número del 0 al 5."*

---

## 3. Pasos de Implementación en Código

### A. Modificar `.env` y `docker-compose.yml`
Agregar las credenciales del modelo de IA:
```env
AI_SOLVER_ENABLED=true
GEMINI_API_KEY=tu_clave_api_aqui
```

### B. Crear el módulo `ai_solver.py`
Un nuevo archivo dedicado solo a contactar a la IA:

```python
import os
import google.generativeai as genai

def resolver_captcha_con_ia(image_path: str) -> int:
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    
    # Configuramos el modelo Gemini 1.5 Pro
    model = genai.GenerativeModel('gemini-1.5-pro')
    
    # Subir imagen
    foto = genai.upload_file(path=image_path)
    
    prompt = "Look at the left reference image, and the right 3D image. Calculate how many clicks on the right arrow are needed so the train on the right matches the orientation of the icon on the left. Output ONLY a number between 0 and 5."
    
    response = model.generate_content([foto, prompt])
    
    try:
        # Limpiar la respuesta para asegurar que es un número
        num = int(response.text.strip())
        return num
    except:
        return -1 # Fallback
```

### C. Modificar `remote_renovar.py`
En el bloque donde actualmente se envía a Telegram, se añade una bifurcación (`if / else`):

```python
if os.getenv("AI_SOLVER_ENABLED") == "true":
    print("🤖 IA Activada: Analizando puzzle...")
    from ai_solver import resolver_captcha_con_ia
    clicks = resolver_captcha_con_ia("captcha.png")
    
    if clicks >= 0 and clicks <= 5:
        print(f"✅ La IA determinó que son {clicks} clics.")
    else:
        print("⚠️ La IA falló. Cayendo al método Telegram...")
        # Lógica de Telegram actual
else:
    # Lógica de Telegram actual (esperar a main.captcha_event.wait())
```

---

## 4. Retos y Consideraciones (Gotchas)

1. **Latencia:** Los modelos Vision como GPT-4o toman entre 3 a 5 segundos en analizar imágenes. Playwright debe tener `wait_for_timeout()` adecuado para evitar que el CAPTCHA expire por inactividad.
2. **Ciclos de "Try Again":** Si la IA falla, Microsoft cargará un nuevo puzzle de 2 rondas. El script debe estar preparado para reiniciar el ciclo de captura de imagen si detecta el texto "Try again" o "That's not quite right".
3. **Costo:** Solucionar vía API no es 100% gratuito, pero el volumen de llamadas de GETCID (cada 25 mins no pide captcha, solo cada 90 días o en baneos) hará que el costo sea apenas un par de centavos al mes.
