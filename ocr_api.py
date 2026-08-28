import cv2
import numpy as np
import pytesseract
import re
import logging

logger = logging.getLogger("OCR_API")
logger.setLevel(logging.INFO)

def extract_iid(text: str):
    # 1. Normalizar
    norm = text.upper()
    norm = re.sub(r'[OQ]', '0', norm)
    norm = re.sub(r'[ILJ|]', '1', norm)
    norm = re.sub(r'Z', '2', norm)
    norm = re.sub(r'[S$]', '5', norm)
    norm = re.sub(r'G', '6', norm)
    norm = re.sub(r'[TY]', '7', norm)
    norm = re.sub(r'B', '8', norm)

    # 2. Convertir no-dígitos en espacios
    clean_text = re.sub(r'\D', ' ', norm)

    # 3. Método exacto: Bloques de 7
    chunks = [c for c in clean_text.split() if c]
    sevens = []
    for c in chunks:
        if len(c) % 7 == 0:
            for i in range(0, len(c), 7):
                sevens.append(c[i:i+7])
        elif len(c) == 63:
            return {"iid": c, "method": "exact-63"}
        elif len(c) == 54:
            return {"iid": c, "method": "exact-54"}
    
    if len(sevens) >= 9:
        return {"iid": "".join(sevens[:9]), "method": "chunk-7-x9"}
    elif len(sevens) >= 8: # Sometimes 54 digit ones have 8 blocks? Actually 54 digits is missing blocks. Let's just do 9 for 63.
        # Office 2013-2021 IID has 63 digits. Windows has 63. Older Office has 54.
        pass

    # 4. Método denso (Fallback)
    # /(?:\d\s{0,2}){62}\d/ en JS
    dense_match = re.search(r'(?:\d\s{0,2}){62}\d', clean_text)
    if dense_match:
        return {"iid": re.sub(r'\s', '', dense_match.group(0)), "method": "dense-cluster-63"}
    
    dense_match_54 = re.search(r'(?:\d\s{0,2}){53}\d', clean_text)
    if dense_match_54:
        return {"iid": re.sub(r'\s', '', dense_match_54.group(0)), "method": "dense-cluster-54"}

    all_digits = re.sub(r'\s', '', clean_text)
    if all_digits:
        return {"iid": all_digits, "method": "partial", "partial": True}

    return None

def process_image(image_bytes: bytes):
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("No se pudo decodificar la imagen")

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Estrategias OpenCV
        strategies = []
        
        # Estrategia 1: Umbralización adaptativa suave (para fondos uniformes)
        resized1 = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        thresh1 = cv2.adaptiveThreshold(resized1, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2)
        strategies.append(("Adaptive_Gaussian", thresh1))

        # Estrategia 2: Umbralización global (para buen contraste)
        resized2 = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
        blurred2 = cv2.GaussianBlur(resized2, (3, 3), 0)
        _, thresh2 = cv2.threshold(blurred2, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        strategies.append(("Otsu_Blur", thresh2))

        # Estrategia 3: Aumento de contraste agresivo + Inversión (A veces los números son blancos sobre fondo oscuro)
        strategies.append(("Inverted_Otsu", cv2.bitwise_not(thresh2)))
        
        # Estrategia 4: Raw Grayscale Resize (Dejar que Tesseract maneje el contraste)
        strategies.append(("Raw_Grayscale", cv2.resize(gray, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_LINEAR)))

        custom_config = r'--oem 3 --psm 11'
        custom_config_whitelist = r'--oem 3 --psm 11 -c tessedit_char_whitelist=0123456789OQILJZS$GTYB \n\r'

        best_partial = None
        
        for name, processed_img in strategies:
            # Primero intentar con whitelist para evitar caracteres basura si la imagen está limpia
            text = pytesseract.image_to_string(processed_img, config=custom_config_whitelist)
            result = extract_iid(text)
            
            if result:
                if not result.get("partial"):
                    return {"success": True, "iid": result["iid"], "method": result["method"], "strategy": f"{name}_Whitelist"}
                else:
                    best_partial = result["iid"]
            
            # Si falló, intentar SIN whitelist (porque la fuente difuminada a veces falla con whitelist)
            text_no_white = pytesseract.image_to_string(processed_img, config=custom_config)
            result_no = extract_iid(text_no_white)
            
            if result_no:
                if not result_no.get("partial"):
                    return {"success": True, "iid": result_no["iid"], "method": result_no["method"], "strategy": f"{name}_NoWhitelist"}
                else:
                    if not best_partial or len(result_no["iid"]) > len(best_partial):
                        best_partial = result_no["iid"]
        
        return {"success": False, "detected_digits": best_partial}
    except Exception as e:
        logger.error(f"Error procesando imagen: {str(e)}")
        return {"success": False, "error": str(e)}
