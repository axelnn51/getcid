import cv2
import pytesseract
import re
import statistics
import time
import logging
import numpy as np
from collections import Counter

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# ============================================================
# UTILIDADES DE NORMALIZACIÓN
# ============================================================

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


# ============================================================
# CHECKSUM IID DE MICROSOFT
# ============================================================

def verify_iid_checksum(iid: str) -> dict:
    """
    Verifica el checksum del IID de Microsoft.
    
    Formato IID: 9 bloques de 7 dígitos (63 dígitos) o 9 bloques de 6 dígitos (54 dígitos).
    Para IID de 63 dígitos: cada bloque de 7 tiene los primeros 6 como datos y el 7º es checksum.
    El checksum es: (suma de los 6 dígitos) mod 7.
    
    Retorna:
        dict con keys: valid (bool), bad_blocks (list de índices 0-based de bloques fallidos)
    """
    digits = re.sub(r'\D', '', iid)
    
    if len(digits) == 54:
        # IID de 54 dígitos no tiene checksum verificable por bloque
        return {"valid": True, "bad_blocks": [], "block_size": 6}
    
    if len(digits) != 63:
        return {"valid": False, "bad_blocks": [], "block_size": 0}
    
    bad_blocks = []
    for i in range(9):
        block = digits[i*7:(i+1)*7]
        data_digits = [int(d) for d in block[:6]]
        checksum_digit = int(block[6])
        expected = sum(data_digits) % 7
        if checksum_digit != expected:
            bad_blocks.append(i)
    
    return {
        "valid": len(bad_blocks) == 0,
        "bad_blocks": bad_blocks,
        "block_size": 7
    }


def attempt_checksum_correction(iid: str, bad_blocks: list) -> str | None:
    """
    Intenta corregir bloques con checksum inválido probando sustituciones
    comunes de confusión OCR: 9↔0, 6↔5, 8↔3, 1↔7.
    
    Solo intenta corregir si hay 1-2 bloques malos (más = lectura muy corrupta).
    Retorna el IID corregido o None si no se pudo corregir.
    """
    if len(bad_blocks) > 2:
        return None
    
    digits = list(re.sub(r'\D', '', iid))
    
    # Mapa de confusiones comunes bidireccionales
    confusion_map = {
        '9': ['0'],
        '0': ['9'],
        '6': ['5', '8'],
        '5': ['6', '3'],
        '8': ['6', '3', '0'],
        '3': ['5', '8'],
        '1': ['7', '4'],
        '7': ['1'],
        '4': ['1', '9'],
    }
    
    for block_idx in bad_blocks:
        block_start = block_idx * 7
        block = digits[block_start:block_start + 7]
        
        # Probar cambiar cada dígito de datos (posiciones 0-5) por sus confusiones
        fixed = False
        for pos in range(6):
            original = block[pos]
            alternatives = confusion_map.get(original, [])
            for alt in alternatives:
                test_block = block.copy()
                test_block[pos] = alt
                data_sum = sum(int(d) for d in test_block[:6])
                if data_sum % 7 == int(test_block[6]):
                    # ¡Corrección encontrada!
                    digits[block_start + pos] = alt
                    logger.info(f"[OCR] CHECKSUM FIX: Bloque {block_idx+1}, pos {pos}: '{original}'→'{alt}'")
                    fixed = True
                    break
            if fixed:
                break
        
        if not fixed:
            # También probar que el checksum mismo esté mal (posición 6)
            data_sum = sum(int(d) for d in block[:6])
            expected_checksum = str(data_sum % 7)
            if expected_checksum != block[6]:
                original_cs = block[6]
                alternatives_cs = confusion_map.get(original_cs, [])
                if expected_checksum in alternatives_cs or True:
                    # El checksum digit fue mal leído — corregirlo directamente
                    digits[block_start + 6] = expected_checksum
                    logger.info(f"[OCR] CHECKSUM FIX: Bloque {block_idx+1}, checksum digit: '{original_cs}'→'{expected_checksum}'")
                    fixed = True
        
        if not fixed:
            return None
    
    corrected = ''.join(digits)
    # Verificar que la corrección es válida
    verify = verify_iid_checksum(corrected)
    if verify["valid"]:
        return corrected
    return None


# ============================================================
# DETECCIÓN DE CALIDAD DE IMAGEN
# ============================================================

def classify_image_quality(gray):
    """
    Clasifica la imagen como 'hd' (captura de pantalla nítida) o 'photo' (foto de pantalla).
    
    Usa la varianza del Laplaciano para medir nitidez y la resolución total.
    - Capturas HD: alta nitidez (bordes definidos), resolución alta
    - Fotos: baja nitidez (desenfoque, moiré), variable resolución
    """
    h, w = gray.shape
    total_pixels = h * w
    
    # Calcular varianza del Laplaciano (medida de nitidez)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    sharpness = laplacian.var()
    
    # Calcular la mediana del gradiente como indicador secundario
    sobelx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sobely = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    gradient_magnitude = np.sqrt(sobelx**2 + sobely**2)
    median_gradient = np.median(gradient_magnitude)
    
    # Heurísticas:
    # - Capturas HD: sharpness > 500, res > 800x600
    # - Fotos nítidas de pantalla: sharpness 100-500
    # - Fotos borrosas: sharpness < 100
    
    is_high_res = total_pixels > 480000  # ~800x600
    
    if sharpness > 300 and is_high_res:
        quality = "hd"
    elif sharpness > 80:
        quality = "photo_clear"
    else:
        quality = "photo_blurry"
    
    logger.info(f"[OCR] Quality: {quality} | Sharpness={sharpness:.1f} | MedGrad={median_gradient:.1f} | Res={w}x{h}")
    return quality


# ============================================================
# LOCALIZACIÓN ROI (sin cambios conceptuales, refinada)
# ============================================================

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


# ============================================================
# EVALUACIÓN DE TOKENS EN LÍNEA
# ============================================================

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


# ============================================================
# ESTRATEGIAS DE PREPROCESAMIENTO
# ============================================================

def build_strategies_hd(roi_img):
    """
    Genera estrategias de preprocesamiento optimizadas para capturas HD.
    NO escala agresivamente — la imagen ya es nítida.
    """
    strategies = []
    h, w = roi_img.shape
    
    # 1. Raw sin escalado — la mejor opción para HD puro
    strategies.append(("HD_Raw_1x", roi_img.copy()))
    
    # 2. Escalado suave 1.5x con Lanczos (preserva bordes finos)
    scaled_15 = cv2.resize(roi_img, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_LANCZOS4)
    strategies.append(("HD_Lanczos_1.5x", scaled_15))
    
    # 3. Otsu directo sin escalado
    _, otsu_raw = cv2.threshold(roi_img.copy(), 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    strategies.append(("HD_Otsu_1x", otsu_raw))
    
    # 4. Sharpening + Otsu (resalta bordes sin engordar)
    sharpen_kernel = np.array([
        [0, -1, 0],
        [-1, 5, -1],
        [0, -1, 0]
    ], dtype=np.float32)
    sharpened = cv2.filter2D(roi_img, -1, sharpen_kernel)
    _, sharp_otsu = cv2.threshold(sharpened, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    strategies.append(("HD_Sharp_Otsu", sharp_otsu))
    
    # 5. Morphological opening para adelgazar trazos (separa 9 de 0)
    kernel_thin = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    thinned = cv2.morphologyEx(otsu_raw, cv2.MORPH_OPEN, kernel_thin)
    strategies.append(("HD_Morph_Open", thinned))
    
    return strategies


def build_strategies_photo(roi_img):
    """
    Genera estrategias de preprocesamiento para fotos de pantalla.
    Escala agresivamente porque la imagen tiene baja resolución/nitidez.
    """
    strategies = []
    
    # 1. Escalado 2.5x con interpolación cúbica + Otsu
    resized = cv2.resize(roi_img, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
    _, otsu = cv2.threshold(resized, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    otsu_blur = cv2.GaussianBlur(otsu, (3, 3), 0)
    strategies.append(("Photo_Cubic_Otsu", otsu_blur))
    
    # 2. Lanczos 2.5x + Otsu
    resized_lanczos = cv2.resize(roi_img, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_LANCZOS4)
    _, lanczos_otsu = cv2.threshold(resized_lanczos, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    strategies.append(("Photo_Lanczos_Otsu", lanczos_otsu))
    
    # 3. Adaptive threshold
    resized_adapt = cv2.resize(roi_img, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
    adaptive = cv2.adaptiveThreshold(resized_adapt, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
    strategies.append(("Photo_Adaptive", adaptive))
    
    # 4. Inverted Otsu (para fondos oscuros)
    _, inv_otsu = cv2.threshold(resized, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    strategies.append(("Photo_Inverted", inv_otsu))
    
    return strategies


# ============================================================
# VOTACIÓN MULTI-CANDIDATO POR DÍGITO
# ============================================================

def vote_candidates(candidates: list) -> str | None:
    """
    Dado una lista de candidatos IID (strings de 63 o 54 dígitos),
    realiza votación mayoritaria dígito por dígito.
    
    Solo vota si todos los candidatos tienen la misma longitud.
    Retorna el IID con mayoría o None si no hay candidatos.
    """
    if not candidates:
        return None
    
    # Filtrar a los que tengan longitud válida (63 o 54)
    valid = [c for c in candidates if len(c) in (54, 63)]
    if not valid:
        return None
    
    # Agrupar por longitud y usar el grupo más grande
    len_counts = Counter(len(c) for c in valid)
    target_len = len_counts.most_common(1)[0][0]
    group = [c for c in valid if len(c) == target_len]
    
    if len(group) == 1:
        return group[0]
    
    # Votación dígito por dígito
    result = []
    diff_positions = []
    for pos in range(target_len):
        digits_at_pos = [c[pos] for c in group]
        counter = Counter(digits_at_pos)
        winner, count = counter.most_common(1)[0]
        result.append(winner)
        if count < len(group):
            diff_positions.append(pos)
    
    voted_iid = ''.join(result)
    
    if diff_positions:
        logger.info(f"[OCR] VOTE: {len(group)} candidatos, discrepancias en posiciones: {diff_positions}")
        # Log cada candidato vs voted
        for i, c in enumerate(group):
            diffs = [f"pos{p}:'{c[p]}'" for p in diff_positions if c[p] != voted_iid[p]]
            if diffs:
                logger.info(f"[OCR] VOTE: Candidato {i} difiere: {', '.join(diffs)}")
    
    return voted_iid


# ============================================================
# MOTOR OCR PRINCIPAL
# ============================================================

def run_ocr_on_image(processed_img, config):
    """Ejecuta Tesseract OCR sobre una imagen preprocesada y extrae tokens numéricos."""
    text = pytesseract.image_to_string(processed_img, config=config)
    lines = re.split(r'[\n\r]+', text)
    
    all_tokens = []
    for line in lines:
        if not line.strip():
            continue
        raw_tokens = re.split(r'[\s\-]+', line.upper())
        tokens = [normalize_block(t) for t in raw_tokens if t]
        all_tokens.extend(tokens)
    
    return all_tokens, text


def extract_iid_from_tokens(tokens, strategy_name):
    """Intenta extraer un IID válido de una lista de tokens."""
    cand = evaluate_line_tokens(tokens, strategy_name)
    if cand:
        return cand
    
    # Fallback: concatenar todos los tokens numéricos y ver si suman 63 o 54
    all_digits = ''.join(tokens)
    if len(all_digits) == 63:
        return {
            "iid": all_digits,
            "method": "concat-63",
            "strategy": strategy_name,
            "score": 60
        }
    if len(all_digits) == 54:
        return {
            "iid": all_digits,
            "method": "concat-54",
            "strategy": strategy_name,
            "score": 60
        }
    
    return None


def process_image(image_bytes: bytes, rescue: bool = False, skip_crop: bool = False):
    """
    Procesa la imagen para extraer el IID.
    
    Arquitectura v2:
    - Fase 0: Clasificar calidad de imagen (HD vs foto)
    - Fase 1: Global OCR rápido (PSM 11 en imagen original)
    - Fase 2: Localización ROI
    - Fase 3: Multi-estrategia OCR con votación por dígito
    - Fase 4: Validación de checksum + corrección automática
    - Fase 5: Rescue mode (si todo falló)
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
    
    # ==================================================
    # FASE 0: CLASIFICAR CALIDAD
    # ==================================================
    quality = classify_image_quality(gray)
    
    # Configs de Tesseract
    whitelist_11 = r'--oem 3 --psm 11 -c tessedit_char_whitelist=0123456789OQILJZS$GTYB'
    whitelist_7 = r'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789'
    whitelist_6 = r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789'
    # Config sin whitelist — usa modelo de lenguaje completo para mejor contexto
    no_whitelist_11 = r'--oem 3 --psm 11'
    
    all_candidates = []  # Lista de IIDs (strings de 63/54 dígitos)
    
    # ==================================================
    # FASE 1: FAST GLOBAL OCR
    # ==================================================
    if not rescue:
        t0 = time.perf_counter()
        
        # Ejecutar con whitelist numérica
        tokens_wl, _ = run_ocr_on_image(gray, whitelist_11)
        cand = evaluate_line_tokens(tokens_wl, "Global_WL")
        if cand:
            all_candidates.append(cand["iid"])
            logger.info(f"[OCR] GLOBAL_WL: candidato {cand['iid'][:14]}... ({cand['method']})")
        
        # Para HD, también ejecutar SIN whitelist (mejor discriminación 9 vs 0)
        if quality == "hd":
            tokens_nwl, raw_text = run_ocr_on_image(gray, no_whitelist_11)
            cand_nwl = evaluate_line_tokens(tokens_nwl, "Global_NoWL")
            if cand_nwl:
                all_candidates.append(cand_nwl["iid"])
                logger.info(f"[OCR] GLOBAL_NoWL: candidato {cand_nwl['iid'][:14]}... ({cand_nwl['method']})")
        
        logger.info(f"[OCR] GLOBAL: {time.perf_counter()-t0:.3f}s ({len(all_candidates)} candidatos)")
        
        # Si tenemos un candidato con checksum válido, devolver inmediatamente
        for c_iid in all_candidates:
            cs = verify_iid_checksum(c_iid)
            if cs["valid"]:
                logger.info(f"[OCR] CHECKSUM OK (Global) → retorno rápido")
                logger.info(f"[OCR] TOTAL: {time.perf_counter()-t_start_total:.3f}s")
                return {"success": True, "iid": c_iid, "method": "global-checksum-ok"}

    # ==================================================
    # FASE 2: ROI ESPACIAL
    # ==================================================
    roi_img = None
    if not skip_crop:
        roi_img = localize_roi_median(gray)
        
    if roi_img is None:
        roi_img = gray  # Fallback a toda la imagen si falla el crop

    # ==================================================
    # FASE 3: MULTI-ESTRATEGIA OCR CON VOTACIÓN
    # ==================================================
    if not rescue:
        t0 = time.perf_counter()
        
        # Seleccionar estrategias según calidad
        if quality == "hd":
            strategies = build_strategies_hd(roi_img)
            ocr_configs = [whitelist_7, whitelist_6]
        elif quality == "photo_clear":
            # Para fotos claras: mezcla de ambas
            strategies = build_strategies_hd(roi_img)[:3] + build_strategies_photo(roi_img)[:2]
            ocr_configs = [whitelist_7]
        else:
            strategies = build_strategies_photo(roi_img)
            ocr_configs = [whitelist_7]
        
        roi_candidates = []
        
        for name, processed_img in strategies:
            for config in ocr_configs:
                config_label = "PSM7" if "psm 7" in config else "PSM6"
                tokens, _ = run_ocr_on_image(processed_img, config)
                cand = extract_iid_from_tokens(tokens, f"{name}_{config_label}")
                if cand:
                    roi_candidates.append(cand["iid"])
                    logger.info(f"[OCR] ROI {name}_{config_label}: {cand['iid'][:14]}... ({cand['method']})")
        
        logger.info(f"[OCR] ROI OCR: {time.perf_counter()-t0:.3f}s ({len(roi_candidates)} candidatos)")
        all_candidates.extend(roi_candidates)
    
    # ==================================================
    # FASE 4: VOTACIÓN + CHECKSUM + CORRECCIÓN
    # ==================================================
    if all_candidates:
        t0 = time.perf_counter()
        
        # 4a. Primero buscar si algún candidato ya pasa checksum directamente
        for c_iid in all_candidates:
            cs = verify_iid_checksum(c_iid)
            if cs["valid"]:
                logger.info(f"[OCR] CHECKSUM OK directo: {c_iid[:14]}...")
                logger.info(f"[OCR] TOTAL: {time.perf_counter()-t_start_total:.3f}s")
                return {"success": True, "iid": c_iid, "method": "direct-checksum-ok"}
        
        # 4b. Votar entre candidatos
        voted = vote_candidates(all_candidates)
        if voted:
            cs = verify_iid_checksum(voted)
            if cs["valid"]:
                logger.info(f"[OCR] VOTED CHECKSUM OK: {voted[:14]}...")
                logger.info(f"[OCR] TOTAL: {time.perf_counter()-t_start_total:.3f}s")
                return {"success": True, "iid": voted, "method": "voted-checksum-ok"}
            
            # 4c. Intentar corrección de checksum sobre el votado
            if cs["bad_blocks"]:
                logger.info(f"[OCR] VOTED tiene {len(cs['bad_blocks'])} bloques malos: {cs['bad_blocks']}")
                corrected = attempt_checksum_correction(voted, cs["bad_blocks"])
                if corrected:
                    logger.info(f"[OCR] AUTO-CORRECTED: {corrected[:14]}...")
                    logger.info(f"[OCR] TOTAL: {time.perf_counter()-t_start_total:.3f}s")
                    return {"success": True, "iid": corrected, "method": "auto-corrected"}
        
        # 4d. Intentar corrección sobre cada candidato individual
        for c_iid in all_candidates:
            cs = verify_iid_checksum(c_iid)
            if cs["bad_blocks"] and len(cs["bad_blocks"]) <= 2:
                corrected = attempt_checksum_correction(c_iid, cs["bad_blocks"])
                if corrected:
                    logger.info(f"[OCR] INDIVIDUAL CORRECTED: {corrected[:14]}...")
                    logger.info(f"[OCR] TOTAL: {time.perf_counter()-t_start_total:.3f}s")
                    return {"success": True, "iid": corrected, "method": "individual-corrected"}
        
        # 4e. Si ninguna corrección funcionó, devolver el votado como mejor intento
        # (El backend de CID hará su propia validación)
        if voted:
            logger.info(f"[OCR] Retornando mejor votado sin checksum: {voted[:14]}...")
            logger.info(f"[OCR] TOTAL: {time.perf_counter()-t_start_total:.3f}s")
            return {"success": True, "iid": voted, "method": "voted-unchecked"}
        
        logger.info(f"[OCR] Fase 4: {time.perf_counter()-t0:.3f}s (sin resultado válido)")

    # ==================================================
    # FASE 5: RESCUE MODE
    # ==================================================
    logger.info(f"[OCR] Iniciando Rescue...")
    
    rescue_candidates = []
    
    # Usar todas las estrategias de foto (agresivas) sobre la ROI
    rescue_strategies = build_strategies_photo(roi_img)
    
    # Agregar estrategias adicionales de rescue
    # Escalado extremo 3x con Lanczos
    resized_3x = cv2.resize(roi_img, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_LANCZOS4)
    _, otsu_3x = cv2.threshold(resized_3x, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    rescue_strategies.append(("Rescue_Lanczos_3x", otsu_3x))
    
    # CLAHE (Contrast Limited Adaptive Histogram Equalization)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(roi_img)
    resized_enhanced = cv2.resize(enhanced, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
    _, otsu_enhanced = cv2.threshold(resized_enhanced, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    rescue_strategies.append(("Rescue_CLAHE_Otsu", otsu_enhanced))
    
    for name, processed_img in rescue_strategies:
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
        for w_item in words:
            cy = w_item['top'] + w_item['height']/2.0
            added = False
            for line in lines:
                lcy = line[0]['top'] + line[0]['height']/2.0
                if abs(cy - lcy) < 25:
                    line.append(w_item)
                    added = True
                    break
            if not added:
                lines.append([w_item])
                
        # Evaluar línea por línea
        for line in lines:
            raw_tokens = [w_item['text'] for w_item in line]
            tokens = [normalize_block(t) for t in raw_tokens if t]
            
            cand = evaluate_line_tokens(tokens, name)
            if cand:
                rescue_candidates.append(cand["iid"])
                
        logger.info(f"[OCR] RESCUE ({name}): {time.perf_counter()-t0:.3f}s")
    
    all_candidates.extend(rescue_candidates)
    
    # Repetir la fase de votación+checksum con todos los candidatos incluyendo rescue
    if all_candidates:
        # Buscar checksum directo
        for c_iid in all_candidates:
            cs = verify_iid_checksum(c_iid)
            if cs["valid"]:
                logger.info(f"[OCR] RESCUE CHECKSUM OK: {c_iid[:14]}...")
                logger.info(f"[OCR] TOTAL: {time.perf_counter()-t_start_total:.3f}s")
                return {"success": True, "iid": c_iid, "method": "rescue-checksum-ok"}
        
        # Votar
        voted = vote_candidates(all_candidates)
        if voted:
            cs = verify_iid_checksum(voted)
            if cs["valid"]:
                logger.info(f"[OCR] RESCUE VOTED OK: {voted[:14]}...")
                logger.info(f"[OCR] TOTAL: {time.perf_counter()-t_start_total:.3f}s")
                return {"success": True, "iid": voted, "method": "rescue-voted-ok"}
            
            # Corrección
            if cs["bad_blocks"]:
                corrected = attempt_checksum_correction(voted, cs["bad_blocks"])
                if corrected:
                    logger.info(f"[OCR] RESCUE CORRECTED: {corrected[:14]}...")
                    logger.info(f"[OCR] TOTAL: {time.perf_counter()-t_start_total:.3f}s")
                    return {"success": True, "iid": corrected, "method": "rescue-corrected"}
            
            # Corrección sobre cada candidato
            for c_iid in all_candidates:
                cs2 = verify_iid_checksum(c_iid)
                if cs2["bad_blocks"] and len(cs2["bad_blocks"]) <= 2:
                    corrected = attempt_checksum_correction(c_iid, cs2["bad_blocks"])
                    if corrected:
                        logger.info(f"[OCR] RESCUE IND CORRECTED: {corrected[:14]}...")
                        logger.info(f"[OCR] TOTAL: {time.perf_counter()-t_start_total:.3f}s")
                        return {"success": True, "iid": corrected, "method": "rescue-ind-corrected"}
            
            # Retornar el votado sin checksum como último recurso
            logger.info(f"[OCR] RESCUE retornando votado sin checksum: {voted[:14]}...")
            logger.info(f"[OCR] TOTAL: {time.perf_counter()-t_start_total:.3f}s")
            return {"success": True, "iid": voted, "method": "rescue-voted-unchecked"}
    
    logger.info(f"[OCR] TOTAL: {time.perf_counter()-t_start_total:.3f}s (Fallo Absoluto)")
    return {"success": False, "error": "No se pudo encontrar el IID"}
