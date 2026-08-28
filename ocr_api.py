import cv2
import numpy as np
import pytesseract
import re
import logging
import time

logger = logging.getLogger("OCR_API")
logger.setLevel(logging.INFO)

def normalize_block(text: str) -> str:
    """Normaliza solo un bloque candidato a IID, convirtiendo letras confusas en números."""
    norm = text.upper()
    norm = re.sub(r'[OQ]', '0', norm)
    norm = re.sub(r'[ILJ|]', '1', norm)
    norm = re.sub(r'Z', '2', norm)
    norm = re.sub(r'[S$]', '5', norm)
    norm = re.sub(r'G', '6', norm)
    norm = re.sub(r'[TY]', '7', norm)
    norm = re.sub(r'B', '8', norm)
    return re.sub(r'\D', '', norm)

def extract_iid(raw_text: str):
    # 1. Separar el texto por caracteres que definitivamente NO pertenecen al IID.
    # El IID solo puede contener números, letras confusas, espacios o guiones.
    # Cualquier otra letra (ej. 'M' de Microsoft) rompe el bloque.
    blocks = re.split(r'[^0-9OQILJZS$GTYB \n\r\t-]', raw_text.upper())
    
    candidates = []
    
    for b in blocks:
        # Limpiar espacios y guiones del bloque para ver su longitud real
        clean_len = len(re.sub(r'[ \n\r\t-]', '', b))
        if clean_len >= 54:
            # Si el bloque aislado tiene suficientes caracteres válidos, es un candidato fuerte.
            # Solo AHORA aplicamos la normalización destructiva (O->0, S->5)
            normalized = normalize_block(b)
            candidates.append(normalized)
            
    for cand in candidates:
        # 2. Bugfix: Comprobar EXACT-63 primero
        if len(cand) == 63:
            return {"iid": cand, "method": "exact-63"}
        elif len(cand) == 54:
            return {"iid": cand, "method": "exact-54"}
        
        # 3. Método bloques de 7
        if len(cand) % 7 == 0:
            if len(cand) >= 63: # 9 bloques
                return {"iid": cand[:63], "method": "chunk-7-x9"}
                
    # 4. Búsqueda densa mejorada (Fallback estricto)
    # Ya no busca en todo el texto, solo en bloques que superaron el filtro
    for b in blocks:
        # Convertimos todo a espacio simple y normalizamos
        norm = normalize_block(b)
        if len(norm) >= 63:
            return {"iid": norm[:63], "method": "dense-cluster-63"}

    # Ya no retornamos 'partial' con basura de 74 dígitos.
    return []

def crop_to_iid_region(gray):
    """
    Intenta encontrar la línea 'Id. de instalación' o 'instalacion'
    usando OCR rápido para recortar la imagen y evitar procesar 'Microsoft Office'.
    """
    try:
        custom_config = r'--oem 3 --psm 11'
        data = pytesseract.image_to_data(gray, config=custom_config, output_type=pytesseract.Output.DICT)
        
        target_y = -1
        for i, text in enumerate(data['text']):
            t = text.lower()
            # ELIMINADO 'id.' porque causa un bug al detectar el 'Id. de confirmación' en el Paso 3
            # y termina recortando el IID fuera de la imagen.
            if 'instal' in t or 'install' in t or 'proporcione' in t:
                y_bottom = data['top'][i] + data['height'][i]
                if y_bottom > target_y:
                    target_y = y_bottom
                    
        if target_y != -1:
            h, w = gray.shape
            # Recortar desde la línea de instrucciones hasta un poco más abajo
            # Específicamente, damos un pequeño margen hacia abajo (~35% de la altura total)
            crop_end = min(h, target_y + int(h * 0.35))
            return gray[target_y:crop_end, :]
    except Exception as e:
        logger.error(f"Error en crop geométrico: {e}")
        
    return gray

def process_image(image_bytes: bytes, rescue: bool = False):
    try:
        t_start_total = time.perf_counter()
        
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("No se pudo decodificar la imagen")

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        t0 = time.perf_counter()
        # 1. CROP GEOMÉTRICO: Aislar la región de los números
        gray = crop_to_iid_region(gray)
        logger.info(f"[OCR] Crop geométrico aplicado: {time.perf_counter() - t0:.3f}s. Dimensiones: {gray.shape}")
        
        # Estrategias OpenCV
        resized1 = cv2.resize(gray, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
        blurred2 = cv2.GaussianBlur(resized1, (3, 3), 0)
        _, thresh2 = cv2.threshold(blurred2, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        fast_strategies = [
            ("Raw_Grayscale", resized1),
            ("Otsu_Blur", thresh2)
        ]
        
        rescue_strategies = [
            ("Adaptive_Gaussian", cv2.adaptiveThreshold(resized1, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2)),
            ("Inverted_Otsu", cv2.bitwise_not(thresh2))
        ]
        
        strategies = rescue_strategies if rescue else fast_strategies

        custom_config_whitelist = r'--oem 3 --psm 11 -c tessedit_char_whitelist=0123456789OQILJZS$GTYB \n\r\t-'
        
        all_candidates = []
        
        for name, processed_img in strategies:
            t0 = time.perf_counter()
            text = pytesseract.image_to_string(processed_img, config=custom_config_whitelist)
            logger.debug(f"[OCR] [{name}] Texto bruto extraído:\n{text}")
            
            blocks = re.split(r'[^0-9OQILJZS$GTYB \n\r\t-]', text.upper())
            candidates_for_strategy = []
            
            for b in blocks:
                clean_len = len(re.sub(r'[ \n\r\t-]', '', b))
                if clean_len >= 54:
                    normalized = normalize_block(b)
                    candidates_for_strategy.append(normalized)
                    
            found_strong_candidate = False
            for cand in candidates_for_strategy:
                if len(cand) == 63:
                    all_candidates.append({"iid": cand, "method": "exact-63", "strategy": name, "score": 100})
                    found_strong_candidate = True
                elif len(cand) == 54:
                    all_candidates.append({"iid": cand, "method": "exact-54", "strategy": name, "score": 95})
                    found_strong_candidate = True
                elif len(cand) % 7 == 0 and len(cand) >= 63:
                    all_candidates.append({"iid": cand[:63], "method": "chunk-7-x9", "strategy": name, "score": 90})
                    found_strong_candidate = True
            
            for b in blocks:
                norm = normalize_block(b)
                if len(norm) >= 63 and not any(c["iid"] == norm[:63] for c in all_candidates):
                    all_candidates.append({"iid": norm[:63], "method": "dense-cluster-63", "strategy": name, "score": 50})
                    
            logger.info(f"[OCR] {name}: {time.perf_counter() - t0:.3f}s → {'Fuerte candidato encontrado' if found_strong_candidate else 'Buscando'}")

            # FAST PATH: Si no estamos en rescate y encontramos un candidato 100% perfecto, paramos.
            if not rescue and found_strong_candidate:
                break

        if not all_candidates:
            if not rescue:
                # Fallback automático: si Fast Path no encuentra absolutamente nada, probamos Rescue directamente
                logger.info("[OCR] Fast Path no encontró nada. Activando Auto-Rescue.")
                return process_image(image_bytes, rescue=True)
            return {"success": False, "error": "No se pudo encontrar un IID válido en la imagen"}
            
        unique_candidates = {}
        for c in all_candidates:
            iid = c["iid"]
            if iid not in unique_candidates or unique_candidates[iid]["score"] < c["score"]:
                unique_candidates[iid] = c
                
        sorted_candidates = sorted(unique_candidates.values(), key=lambda x: x["score"], reverse=True)
        
        t_total = time.perf_counter() - t_start_total
        logger.info(f"[OCR] TOTAL: {t_total:.3f}s. Candidatos únicos: {[c['iid'] for c in sorted_candidates]}")
        
        return {
            "success": True,
            "iid": sorted_candidates[0]["iid"],
            "method": sorted_candidates[0]["method"],
            "strategy": sorted_candidates[0]["strategy"],
            "candidates": sorted_candidates,
            "rescue_mode": rescue
        }
    except Exception as e:
        logger.error(f"Error procesando imagen: {str(e)}")
        return {"success": False, "error": str(e)}
