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
                "REGLA: Tienes que mover el tren por las vías hasta llegar al icono objetivo indicado en la imagen izquierda. "
                "CÓMO CALCULAR LOS CLICS: "
                "1. Encuentra el icono objetivo en las vías de la imagen derecha. "
                "2. Encuentra dónde está el tren actualmente. "
                "3. Fíjate hacia dónde mira la parte frontal del tren (su 'cara' o ventana). Esa es la dirección 'hacia adelante' por la que avanzará. "
                "4. Sigue la vía (línea roja punteada) hacia adelante. ¡CUIDADO EXTREMO! Las vías pueden cruzarse sobre sí mismas en 3D (como un puente, un paso a desnivel o una figura de 8). Sigue la vía de forma continua por arriba o por abajo sin 'saltar' bruscamente en los cruces. "
                "5. Cuenta cuántos postes rojos (paradas) hay desde el tren hasta el objetivo siguiendo esa ruta exacta. "
                "6. CIRCUITO CÍCLICO: Si siguiendo la vía llegas al final de la pista, el siguiente clic te teletransporta al extremo inicial de la vía para continuar el bucle. "
                "Ejemplo: Si el camino es A -> B -> C -> D, y el tren está en D. 1 clic lo lleva a A. 2 clics lo llevan a B. "
                "NUNCA calcules la distancia 'más corta' en línea recta. SIEMPRE sigue el camino de las vías paso a paso. "
                "¡ATENCIÓN! La posición inicial NUNCA es la correcta. La respuesta NUNCA es 0. (Siempre es del 1 al 5).\n\n"
                "TIPO 2: ROTACIÓN (ej. 'Use the arrows to rotate the object to face the same direction as the hand'). "
                "REGLA: Calcula cuántos clics a la derecha necesitas para que el objeto apunte en la misma dirección que la mano. (NUNCA es 0).\n\n"
                "Analiza paso a paso, y en la ÚLTIMA LÍNEA de tu respuesta escribe ÚNICAMENTE el número final de clics (del 1 al 5)."
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
