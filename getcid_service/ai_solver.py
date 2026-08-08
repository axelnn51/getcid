import os
import json
import random
import logging
from PIL import Image

# Usando el nuevo SDK genai
from google import genai
from google.genai import types

logger = logging.getLogger("AISolver")

def resolver_captcha_con_ia(image_path: str, attempt: int = 1) -> int:
    """
    Toma un screenshot del CAPTCHA de Arkose Labs, lo envía a Gemini,
    y le pide que cuente cuántos clics a la derecha se necesitan.
    Usa JSON estructurado para mayor precisión.
    """
    api_key_env = os.getenv("GEMINI_API_KEY")
    if not api_key_env or api_key_env == "tu_clave_api_aqui":
        logger.error("❌ GEMINI_API_KEY no está configurada o es inválida.")
        return -1
        
    api_keys = [k.strip() for k in api_key_env.split(",") if k.strip()]
    random.shuffle(api_keys)
    
    # Temperatura variable: primer intento conservador, intentos subsiguientes más creativos
    temperature = 0.2 if attempt == 1 else 0.6
    
    for idx, api_key in enumerate(api_keys):
        try:
            client = genai.Client(api_key=api_key)
            # Usamos gemini-2.5-flash ya que los modelos 1.5 devuelven 404 en estas llaves
            model_id = 'gemini-2.5-flash'
            
            if idx == 0:
                logger.info(f"🧠 IA: Analizando con {model_id} (temp={temperature}, key={idx+1}/{len(api_keys)})...")
            
            img = Image.open(image_path)
            
            prompt = (
                "Eres un experto visual. Resuelve este CAPTCHA de Arkose Labs.\n"
                "La imagen tiene dos partes: una imagen izquierda y una imagen derecha.\n"
                "1. IDENTIFICA el ICONO OBJETIVO en la imagen de la izquierda (ej. una mano, estrella, hongo, etc.). ¡No te confundas con los iconos de la derecha!\n"
                "2. UBICA el tren azul en el circuito de vías de la derecha.\n"
                "3. CUENTA cuántos avances HACIA LA DERECHA (nodo por nodo) se necesitan para mover el tren hasta el ICONO OBJETIVO.\n"
                "REGLA DE ORO: El tren NUNCA empieza en el nodo objetivo. El número de clics NUNCA ES CERO (0). Si crees que es 0, miraste mal el icono de la izquierda. Vuelve a mirar.\n\n"
                "Tu salida DEBE ser estrictamente JSON válido con las claves:\n"
                "- 'reasoning': Explica qué icono ves a la izquierda, en qué icono está el tren, y tu conteo.\n"
                "- 'clicks': Un número entero entre 1 y 5.\n"
            )
            
            # Usar response_mime_type para forzar JSON
            config = types.GenerateContentConfig(
                temperature=temperature,
                response_mime_type="application/json"
            )
            
            response = client.models.generate_content(
                model=model_id,
                contents=[prompt, img],
                config=config
            )
            
            text_res = response.text.strip()
            
            try:
                with open("last_reasoning.txt", "w", encoding="utf-8") as f:
                    f.write(text_res)
            except:
                pass
                
            try:
                data = json.loads(text_res)
                logger.info(f"🧠 Razonamiento IA: {data.get('reasoning', 'N/A')}")
                clicks = int(data.get('clicks', -1))
                if 1 <= clicks <= 5:
                    return clicks
                elif clicks == 0:
                    logger.warning(f"⚠️ La IA devolvió 0 (imposible). Adivinando entre 1 y 5...")
                    return random.randint(1, 5)
                else:
                    logger.warning(f"⚠️ Clics fuera de rango: {clicks}")
            except json.JSONDecodeError:
                logger.warning(f"⚠️ La respuesta no fue JSON válido: {text_res}")
                
            return -1
            
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "quota" in error_msg.lower() or "403" in error_msg:
                logger.warning(f"⚠️ API Key {idx+1}/{len(api_keys)} falló por cuota. Probando siguiente...")
                continue
            else:
                # Tratar de hacer fallback a gemini-2.5-flash si falla por modelo no encontrado
                if "not found" in error_msg.lower() or "invalid model" in error_msg.lower():
                    logger.warning("⚠️ Modelo no disponible, reintentando con gemini-2.5-flash...")
                    try:
                        response = client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=[prompt, img],
                            config=config
                        )
                        text_res = response.text.strip()
                        try:
                            with open("last_reasoning.txt", "w", encoding="utf-8") as f:
                                f.write(text_res)
                        except:
                            pass
                        data = json.loads(text_res)
                        logger.info(f"🧠 Razonamiento IA (fallback flash): {data.get('reasoning', 'N/A')}")
                        return int(data.get('clicks', -1))
                    except Exception as fallback_e:
                        logger.error(f"❌ Fallback a flash también falló: {fallback_e}")
                        return -1
                else:
                    logger.error(f"❌ Error API: {e}")
                    return -1
                
    logger.error("❌ Todas las llaves de Gemini fallaron.")
    return -1
