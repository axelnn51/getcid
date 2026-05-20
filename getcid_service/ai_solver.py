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
    api_key_env = os.getenv("GEMINI_API_KEY")
    if not api_key_env or api_key_env == "tu_clave_api_aqui":
        logger.error("❌ GEMINI_API_KEY no está configurada o es inválida.")
        return -1
        
    # Extraer todas las llaves separadas por coma
    api_keys = [k.strip() for k in api_key_env.split(",") if k.strip()]
    
    # Barajar aleatoriamente para distribuir la carga entre las llaves
    import random
    random.shuffle(api_keys)
    
    for idx, api_key in enumerate(api_keys):
        try:
            genai.configure(api_key=api_key)
            
            # Usamos el modelo optimizado para visión (Flash es gratis y súper rápido)
            model = genai.GenerativeModel('gemini-2.5-flash')
            
            if idx == 0:
                logger.info(f"🤖 Enviando imagen a Gemini Vision (Intentando con API Key {idx+1}/{len(api_keys)})...")
            
            # Cargar la imagen local
            img = Image.open(image_path)
            
            # El prompt perfecto adaptativo con reglas explícitas para evitar alucinaciones
            prompt = (
                "Eres un experto solucionando puzzles lógicos de CAPTCHAs de Arkose Labs. "
                "Primero LEE LA INSTRUCCIÓN que aparece en texto en la imagen. "
                "Existen varios tipos de puzzles. Sigue estas reglas ESTRICTAS según la instrucción:\n\n"
                "TIPO 1: POSICIÓN EN EL CAMINO (ej. 'Make sure the train... is at the position of the icon connected by a red line'). "
                "REGLA: La imagen izquierda muestra un icono objetivo. "
                "La imagen derecha muestra unas vías de tren 3D con una línea roja punteada y varios iconos a lo largo de la ruta. "
                "¡ATENCIÓN A LA PROFUNDIDAD 3D! No leas los iconos simplemente de izquierda a derecha en la pantalla. Debes seguir visualmente el recorrido curvo de las vías del tren (la línea roja punteada) desde la posición actual del tren hacia adelante. "
                "Cuenta cuántas 'paradas' (iconos) a lo largo de esa ruta 3D exacta debe avanzar el tren para llegar al icono objetivo. "
                "¡ATENCIÓN! La posición inicial NUNCA es la correcta en estos CAPTCHAs. La respuesta NUNCA es 0. Siempre debes moverlo (de 1 a 5 clics).\n\n"
                "TIPO 2: ROTACIÓN (ej. 'Use the arrows to rotate the object to face the same direction as the hand'). "
                "REGLA: Calcula cuántos clics a la derecha necesitas para que el objeto apunte en la misma dirección que la mano. (NUNCA es 0).\n\n"
                "Analiza paso a paso, y en la ÚLTIMA LÍNEA de tu respuesta escribe ÚNICAMENTE el número final de clics (del 1 al 5)."
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
            error_msg = str(e)
            if "429" in error_msg or "Quota" in error_msg or "403" in error_msg:
                logger.warning(f"⚠️ API Key {idx+1}/{len(api_keys)} falló (429/403). Probando la siguiente si existe...")
                continue
            else:
                logger.error(f"❌ Error al contactar con la API de Gemini: {e}")
                return -1
                
    logger.error("❌ Todas las llaves de Gemini fallaron o están sin cuota.")
    return -1
