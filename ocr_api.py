import cv2
import pytesseract
import re
import statistics
import time
import logging
import numpy as np

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def normalize_block(text: str) -> str:
    """Normaliza un bloque candidato a IID, convirtiendo letras confusas en números."""
    norm = text.upper()
    norm = re.sub(r'[OQ]', '0', norm)
    norm = re.sub(r'[ILJ|]', '1', norm)
    norm = re.sub(r'Z', '2', norm)
    norm = re.sub(r'[S$]', '5', norm)
    norm = re.sub(r'G', '6', norm)
    norm = re.sub(r'[TY]', '7', norm)
    norm = re.sub(r'B', '8', norm)
    return re.sub(r'\D', '', norm)

def evaluate_line_tokens(tokens, strategy_name):
    """
    Evalúa una lista de tokens (palabras normalizadas de una sola línea)
    y retorna el mejor candidato (dict) o None si no es válido.
    Prioridad:
    1. 9 bloques exactos de 7
    2. Cadena continua de 63
    3. Cadena continua de 54
    """
    # 1. 9 bloques exactos de 7
    for i in range(len(tokens) - 8):
        sequence = tokens[i:i+9]
        if all(len(tk) == 7 for tk in sequence):
            return {
                "iid": "".join(sequence),
                "method": "perfect-9x7",
                "strategy": strategy_name,
                "score": 100
            }
            
    # 2. Búsqueda de tokens continuos (63 o 54)
    for tk in tokens:
        if len(tk) == 63:
            return {
                "iid": tk,
                "method": "exact-63-token",
                "strategy": strategy_name,
                "score": 80
            }
        if len(tk) == 54:
            return {
                "iid": tk,
                "method": "exact-54-token",
                "strategy": strategy_name,
                "score": 80
            }
            
    return None

def localize_roi_median(gray):
    """
    Utiliza image_to_data para encontrar la línea con más números,
    basándose en la mediana de los centros Y y las alturas para trazar
    una banda horizontal estrecha perfecta.
    """
    h, w = gray.shape
    try:
        custom_config = r'--oem 3 --psm 11'
        data = pytesseract.image_to_data(gray, config=custom_config, output_type=pytesseract.Output.DICT)
        
        number_words = []
        for i, text in enumerate(data['text']):
            clean = re.sub(r'\D', '', text)
            if len(clean) >= 5:
                number_words.append(i)
                
        if len(number_words) >= 3:
            # Calcular centro Y de cada token numérico
            centers_y = [data['top'][i] + data['height'][i]/2.0 for i in number_words]
            
            # Agrupar por línea usando los centros Y con una tolerancia
            clusters = {}
            for i, cy in zip(number_words, centers_y):
                found = False
                for k in clusters.keys():
                    if abs(cy - k) < 20: # 20px tolerancia
                        clusters[k].append(i)
                        found = True
                        break
                if not found:
                    clusters[cy] = [i]
                    
            # La línea ganadora será la que tenga MÁS palabras numéricas
            best_cluster_center = max(clusters, key=lambda k: len(clusters[k]))
            best_words = clusters[best_cluster_center]
            
            # Calcular medianas para ignorar bounding boxes anormalmente altos
            y_centers = [data['top'][i] + data['height'][i]/2.0 for i in best_words]
            heights = [data['height'][i] for i in best_words]
            
            median_y = statistics.median(y_centers)
            median_h = statistics.median(heights)
            
            # Construir la banda
            half_h = median_h / 2.0
            min_top = int(median_y - half_h - 15) # Margen superior (15px)
            max_bottom = int(median_y + half_h + 15) # Margen inferior (15px)
            
            min_top = max(0, min_top)
            max_bottom = min(h, max_bottom)
            
            cropped = gray[min_top:max_bottom, :]
            
            if cropped.shape[0] >= 30:
                logger.info(f"[OCR] ROI: {cropped.shape[1]},{cropped.shape[0]} at y={min_top}:{max_bottom}")
                return cropped
            else:
                logger.warning(f"[OCR] ROI detectada muy estrecha ({cropped.shape[0]}px). Ignorando crop.")
                
    except Exception as e:
        logger.error(f"Error en localización espacial (ROI): {e}")
        
    logger.info(f"[OCR] Imposible localizar línea numérica. ROI=None")
    return None

def process_image(image_bytes: bytes, rescue: bool = False, skip_crop: bool = False):
    """
    Procesa la imagen para extraer el IID usando el enfoque Global OCR First, ROI Second.
    - Fase 1: Global OCR (PSM 11 en imagen original)
    - Fase 2: Localización Espacial de ROI (Median Y)
    - Fase 3: OCR de ROI (Raw y Otsu con PSM 7)
    - Fase 4: Rescue (Adaptive e Inverted con image_to_data en líneas visuales separadas)
    """
    t_start_total = time.perf_counter()
    
    # 1. Cargar imagen
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return {"success": False, "error": "No se pudo decodificar la imagen"}
        
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    logger.info(f"[OCR] Procesando imagen: {w}x{h} px | Rescue={rescue}")
    
    # Whitelist que permite confusión (para luego limpiar)
    whitelist_11 = r'--oem 3 --psm 11 -c tessedit_char_whitelist=0123456789OQILJZS$GTYB \n\r\t-'
    # Whitelist estricta para PSM 7 (solo números, porque la ROI ya debería estar limpia)
    whitelist_7 = r'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789 \n\r\t-'

    # ==================================================
    # FASE 1: FAST GLOBAL OCR (Solo si NO estamos en Rescue)
    # ==================================================
    if not rescue:
        t0 = time.perf_counter()
        text_global = pytesseract.image_to_string(gray, config=whitelist_11)
        
        # Procesar por líneas, manteniendo los saltos de línea intactos
        lines = re.split(r'[\n\r]+', text_global)
        for line in lines:
            if not line.strip():
                continue
            raw_tokens = re.split(r'[\s\-]+', line.upper())
            tokens = [normalize_block(t) for t in raw_tokens if t]
            
            cand = evaluate_line_tokens(tokens, "Global_Fast")
            if cand:
                logger.info(f"[OCR] GLOBAL: {time.perf_counter()-t0:.3f}s")
                logger.info(f"[OCR] GLOBAL_CANDIDATE: {cand['iid']} ({cand['method']})")
                logger.info(f"[OCR] TOTAL: {time.perf_counter()-t_start_total:.3f}s")
                return {"success": True, "iid": cand["iid"], "method": cand["method"]}
                
        logger.info(f"[OCR] GLOBAL: {time.perf_counter()-t0:.3f}s (Sin éxito)")

    # ==================================================
    # FASE 2: ROI ESPACIAL
    # ==================================================
    roi_img = None
    if not skip_crop:
        roi_img = localize_roi_median(gray)
        
    if roi_img is None:
        roi_img = gray # Fallback a toda la imagen si falla el crop

    # ==================================================
    # FASE 3: OCR DE ROI (Raw y Otsu)
    # ==================================================
    if not rescue:
        # Preprocesamientos para Fase 3
        strategies = [
            ("ROI_Raw", cv2.resize(roi_img, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC))
        ]
        
        # Aplicamos Otsu
        resized_for_otsu = cv2.resize(roi_img, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
        _, otsu = cv2.threshold(resized_for_otsu, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        otsu_blur = cv2.GaussianBlur(otsu, (3,3), 0)
        strategies.append(("ROI_Otsu", otsu_blur))
        
        for name, processed_img in strategies:
            t0 = time.perf_counter()
            # PSM 7 asume una sola línea de texto
            text_roi = pytesseract.image_to_string(processed_img, config=whitelist_7)
            
            raw_tokens = re.split(r'[\s\-]+', text_roi.upper())
            tokens = [normalize_block(t) for t in raw_tokens if t]
            
            cand = evaluate_line_tokens(tokens, name)
            if cand:
                logger.info(f"[OCR] ROI OCR ({name}): {time.perf_counter()-t0:.3f}s")
                logger.info(f"[OCR] TOTAL: {time.perf_counter()-t_start_total:.3f}s")
                return {"success": True, "iid": cand["iid"], "method": cand["method"]}
                
            logger.info(f"[OCR] ROI OCR ({name}): {time.perf_counter()-t0:.3f}s (Sin éxito)")

    # ==================================================
    # FASE 4: RESCUE
    # ==================================================
    # Entra si rescue=True o si falló todo lo de arriba
    logger.info(f"[OCR] Iniciando Rescue...")
    
    # Preprocesamientos para Rescue
    strategies = []
    resized_for_rescue = cv2.resize(roi_img, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
    
    adaptive = cv2.adaptiveThreshold(resized_for_rescue, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
    strategies.append(("Rescue_Adaptive", adaptive))
    
    _, inv_otsu = cv2.threshold(resized_for_rescue, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    strategies.append(("Rescue_Inverted", inv_otsu))
    
    rescue_candidates = []
    
    for name, processed_img in strategies:
        t0 = time.perf_counter()
        
        # Usar image_to_data para conservar la posición vertical de las palabras
        data = pytesseract.image_to_data(processed_img, config=whitelist_11, output_type=pytesseract.Output.DICT)
        
        words = []
        for i, txt in enumerate(data['text']):
            txt = txt.strip()
            if txt:
                words.append({'text': txt, 'top': data['top'][i], 'height': data['height'][i]})
                
        # Agrupar por línea usando el centro Y (tolerancia 25px)
        lines = []
        for w in words:
            cy = w['top'] + w['height']/2.0
            added = False
            for line in lines:
                lcy = line[0]['top'] + line[0]['height']/2.0
                if abs(cy - lcy) < 25:
                    line.append(w)
                    added = True
                    break
            if not added:
                lines.append([w])
                
        # Evaluar línea por línea
        for line in lines:
            raw_tokens = [w['text'] for w in line]
            tokens = [normalize_block(t) for t in raw_tokens if t]
            
            cand = evaluate_line_tokens(tokens, name)
            if cand:
                rescue_candidates.append(cand)
                
        logger.info(f"[OCR] RESCUE ({name}): {time.perf_counter()-t0:.3f}s")
        
    if rescue_candidates:
        # Priorizar perfect-9x7 sobre tokens continuos
        rescue_candidates.sort(key=lambda x: x["score"], reverse=True)
        best_cand = rescue_candidates[0]
        
        logger.info(f"[OCR] RESCUE_CANDIDATE: {best_cand['iid']} ({best_cand['method']} via {best_cand['strategy']})")
        logger.info(f"[OCR] TOTAL: {time.perf_counter()-t_start_total:.3f}s")
        return {"success": True, "iid": best_cand["iid"], "method": best_cand["method"]}
        
    logger.info(f"[OCR] TOTAL: {time.perf_counter()-t_start_total:.3f}s (Fallo Absoluto)")
    
    if not rescue:
        # Auto-activar Rescue si veníamos del flujo normal y fallamos
        return process_image(image_bytes, rescue=True, skip_crop=True)
        
    return {"success": False, "error": "No se pudo encontrar el IID"}

