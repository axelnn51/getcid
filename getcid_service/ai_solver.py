import os
import re
import random
import logging
import google.generativeai as genai
from PIL import Image

logger = logging.getLogger("AISolver")

def resolver_captcha_con_ia(image_path: str) -> int:
    """
    Toma un screenshot del CAPTCHA de Arkose Labs, lo envía a Gemini 1.5 Pro,
    y le pide que cuente cuántos clics a la derecha se necesitan para alinear la imagen.
    Devuelve un entero (0-5) o -1 si falla.
    """
    api_key_env = os.getenv("GEMINI_API_KEY")
    if not api_key_env or api_key_env == "tu_clave_api_aqui":
        logger.error("❌ GEMINI_API_KEY no está configurada o es inválida.")
        return -1
        
    # Extraer todas las llaves separadas por coma
    api_keys = [k.strip() for k in api_key_env.split(",") if k.strip()]
    
    # Barajar aleatoriamente para distribuir la carga entre las llaves
    random.shuffle(api_keys)
    
    for idx, api_key in enumerate(api_keys):
        try:
            genai.configure(api_key=api_key)
            
            # Usamos gemini-2.5-flash porque gemini-2.5-pro no está disponible en la capa gratuita (Free Tier).
            model = genai.GenerativeModel('gemini-2.5-flash')
            
            if idx == 0:
                logger.info(f"🤖 Enviando imagen a Gemini Vision (Intentando con API Key {idx+1}/{len(api_keys)})...")
            
            # Cargar la imagen local
            img = Image.open(image_path)
            
            # El prompt estructurado paso a paso para forzar a la IA a enumerar los nodos
            prompt = (
                "Eres un experto solucionando puzzles lógicos de CAPTCHAs de Arkose Labs. "
                "Existen dos tipos principales. Lee la instrucción de la imagen para saber cuál es:\n\n"
                "TIPO 1: POSICIÓN EN EL CAMINO (ej. 'move the train to the icon').\n"
                "La imagen contiene dos partes: a la izquierda el icono objetivo, a la derecha un tren en una vía 3D con varios postes rojos que tienen iconos. "
                "REGLA: Tienes que calcular cuántos clics ('avances') se necesitan para mover el tren por la vía hasta el icono objetivo.\n"
                "Sigue ESTRICTAMENTE este análisis paso a paso:\n"
                "1. IDENTIFICA EL OBJETIVO: ¿Cuál es el icono de la imagen izquierda?\n"
                "2. POSICIÓN INICIAL: ¿Sobre qué icono (poste rojo) está estacionado el tren AZUL en la imagen derecha?\n"
                "3. DIRECCIÓN: ¿Hacia dónde mira la cara frontal (la chimenea/ventana) del tren?\n"
                "4. MAPEO DE LA VÍA: Sigue la línea punteada roja en la dirección que mira el tren. Enumera TODOS los iconos (postes) que encuentras en el camino, en orden, hasta llegar al icono objetivo. ¡No te saltes ninguno! La vía puede cruzarse o ser un bucle circular.\n"
                "5. CONTEO: Cuenta los pasos (clics). Cada avance a un nuevo poste es 1 clic.\n"
                "Ejemplo de razonamiento esperado:\n"
                "- Objetivo: Diamante.\n"
                "- Posición inicial: El tren está sobre el icono de una taza.\n"
                "- Dirección: Mira hacia la derecha.\n"
                "- Camino: Taza -> (clic 1) -> Engranaje -> (clic 2) -> Cadenas -> (clic 3) -> Diamante.\n"
                "- Total clics: 3.\n\n"
                "TIPO 2: ROTACIÓN (ej. 'Use the arrows to rotate the object to face the same direction as the hand').\n"
                "REGLA: Calcula cuántos clics a la derecha necesitas para que el animal u objeto de la derecha apunte en la misma dirección exacta que la mano de la izquierda.\n\n"
                "REGLAS CRÍTICAS:\n"
                "- Para el tren: ¡NUNCA vayas en reversa! Siempre sigue la dirección a la que apunta el frente del tren.\n"
                "- La posición inicial NUNCA es la correcta. La respuesta NUNCA es 0.\n"
                "- La respuesta final siempre es un número del 1 al 5.\n"
                "En la ÚLTIMA LÍNEA de tu respuesta escribe ÚNICAMENTE el número final de clics (ejemplo: 3)."
            )
            
            response = model.generate_content([prompt, img])
            
            # Extraer y limpiar el resultado
            text_res = response.text.strip()
            logger.info(f"🤖 IA Respuesta RAW: '{text_res}'")
            try:
                with open("last_reasoning.txt", "w", encoding="utf-8") as f:
                    f.write(text_res)
            except:
                pass
            
            
            # Extraemos solo el ÚLTIMO número de la respuesta (para evitar que agarre números de listas como '1.')
            numbers = re.findall(r'\d+', text_res)
            if numbers:
                num = int(numbers[-1])
                if 0 <= num <= 5:
                    return num
                    
            logger.warning(f"⚠️ La respuesta de la IA no fue un número válido (0-5): {text_res}")
            return -1
            
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "Quota" in error_msg or "403" in error_msg:
                logger.warning(f"⚠️ API Key {idx+1}/{len(api_keys)} falló (429/403). Probando la siguiente si existe...")
                continue
            else:
                logger.error(f"❌ Error al contactar con la API de Gemini: {e}")
                return -1
                
    logger.error("❌ Todas las llaves de Gemini fallaron o están sin cuota.")
    return -1
