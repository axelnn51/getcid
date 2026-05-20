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
        
        # Usamos el modelo optimizado para visión
        model = genai.GenerativeModel('gemini-1.5-pro')
        
        logger.info("🤖 Enviando imagen a Gemini 1.5 Pro para análisis de CAPTCHA...")
        
        # Cargar la imagen local
        img = Image.open(image_path)
        
        # El prompt perfecto según las instrucciones
        prompt = (
            "Eres un experto solucionando puzzles lógicos. En la imagen adjunta hay un puzzle de 2 partes. "
            "A la izquierda hay un ícono de referencia mostrando una dirección específica. "
            "A la derecha hay un objeto/tren sobre unas vías en una imagen 3D. "
            "Tu tarea es determinar cuántos 'clics' en el botón de flecha derecha se necesitan para rotar la imagen de la derecha "
            "y que coincida EXACTAMENTE con la orientación y el ángulo de la imagen de referencia de la izquierda. "
            "Sabiendo que cada clic a la flecha rota la imagen una cantidad fija, dime el número exacto de clics. "
            "IMPORTANTE: Tu respuesta debe ser ÚNICAMENTE el número entero (ejemplo: 3). No escribas ninguna otra palabra, solo el número del 0 al 5."
        )
        
        response = model.generate_content([prompt, img])
        
        # Extraer y limpiar el resultado
        text_res = response.text.strip()
        logger.info(f"🤖 IA Respuesta RAW: '{text_res}'")
        
        # Extraemos solo los números por si la IA añade puntuación
        import re
        numbers = re.findall(r'\d+', text_res)
        if numbers:
            num = int(numbers[0])
            if 0 <= num <= 5:
                return num
                
        logger.warning(f"⚠️ La respuesta de la IA no fue un número válido (0-5): {text_res}")
        return -1
        
    except Exception as e:
        logger.error(f"❌ Error al contactar con la API de Gemini: {e}")
        return -1
