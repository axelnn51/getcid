import os
import google.generativeai as genai
import logging
from PIL import Image

logger = logging.getLogger("AISolver")

def resolver_captcha_con_ia(image_path: str) -> int:
    """
    Toma un screenshot del CAPTCHA de Arkose Labs, lo envía a Gemini 1.5 Pro,
    y le pide que cuente cuántos clics a la derecha se necesitan para alinear la imagen.
    Devuelve un entero (0-5) o -1 si falla.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or api_key == "tu_clave_api_aqui":
        logger.error("❌ GEMINI_API_KEY no está configurada o es inválida.")
        return -1
        
    try:
        genai.configure(api_key=api_key)
        
        # Usamos el modelo optimizado para visión (Flash es gratis y súper rápido)
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        logger.info("🤖 Enviando imagen a Gemini 1.5 Pro para análisis de CAPTCHA...")
        
        # Cargar la imagen local
        img = Image.open(image_path)
        
        # El prompt perfecto adaptativo a cualquier puzzle de Arkose
        prompt = (
            "Eres un experto solucionando puzzles lógicos de CAPTCHAs de Arkose Labs. "
            "En la imagen adjunta hay un puzzle. Primero LEE LA INSTRUCCIÓN que aparece en texto en la imagen "
            "(ej. 'Make sure the train in the image is at the position of the icon...', 'Use the arrows to rotate...', etc). "
            "Luego, resuelve el puzzle estrictamente según esa instrucción comparando la imagen izquierda (referencia) y la derecha (interactiva). "
            "Determina cuántos clics a la flecha DERECHA se necesitan (del 0 al 5) para llegar a la solución correcta. "
            "Analiza paso a paso, y en la ÚLTIMA LÍNEA de tu respuesta escribe ÚNICAMENTE el número final de clics."
        )
        
        response = model.generate_content([prompt, img])
        
        # Extraer y limpiar el resultado
        text_res = response.text.strip()
        logger.info(f"🤖 IA Respuesta RAW: '{text_res}'")
        
        # Extraemos solo el ÚLTIMO número de la respuesta (para evitar que agarre números de listas como '1.')
        import re
        numbers = re.findall(r'\d+', text_res)
        if numbers:
            num = int(numbers[-1])
            if 0 <= num <= 5:
                return num
                
        logger.warning(f"⚠️ La respuesta de la IA no fue un número válido (0-5): {text_res}")
        return -1
        
    except Exception as e:
        logger.error(f"❌ Error al contactar con la API de Gemini: {e}")
        return -1
