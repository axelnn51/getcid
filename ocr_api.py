import cv2
import numpy as np
import pytesseract
import re
import logging
import time

logger = logging.getLogger("OCR_API")
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


def crop_to_iid_region(gray):
    """
    Intenta encontrar la línea con mayor concentración de números (el IID)
    usando OCR rápido para recortar SOLO la banda horizontal que los contiene.
    Esto permite usar --psm 7 (una sola línea de texto) en las fases posteriores.
    """
    h, w = gray.shape
    
    # 1. Protección para imágenes ya recortadas
    if h <= 150 and (w / h) >= 3.0:
        logger.info(f"[OCR] Imagen detectada como pre-recortada ({w}x{h}). Omitiendo crop geométrico.")
        return gray

    try:
        custom_config = r'--oem 3 --psm 11'
        data = pytesseract.image_to_data(gray, config=custom_config, output_type=pytesseract.Output.DICT)
        
        # 2. Buscar palabras que parezcan números (longitud >= 5, mayormente dígitos)
        number_words = []
        for i, text in enumerate(data['text']):
            clean = re.sub(r'\D', '', text)
            if len(clean) >= 5:
                number_words.append(i)
                
        # 3. Agrupar palabras por línea horizontal (top cercano)
        if len(number_words) >= 3: # Si hay suficientes números para deducir una línea
            tops = [data['top'][i] for i in number_words]
            clusters = {}
            for t in tops:
                found = False
                for k in clusters.keys():
                    if abs(t - k) < 20: # 20px de tolerancia vertical
                        clusters[k].append(t)
                        found = True
                        break
                if not found:
                    clusters[t] = [t]
            
            # La línea del IID será la que tenga MÁS palabras numéricas
            best_cluster_top = max(clusters, key=lambda k: len(clusters[k]))
            
            # 4. Encontrar min_top y max_bottom de esa línea
            min_top = min([data['top'][i] for i in number_words if abs(data['top'][i] - best_cluster_top) < 20])
            max_bottom = max([data['top'][i] + data['height'][i] for i in number_words if abs(data['top'][i] - best_cluster_top) < 20])
            
            # Margen de seguridad ajustado (no muy grande para no incluir otras líneas)
            min_top = max(0, min_top - 5)
            max_bottom = min(h, max_bottom + 10)
            
            cropped = gray[min_top:max_bottom, :]
            
            if cropped.shape[0] < 30:
                logger.warning(f"[OCR] Crop de banda demasiado estrecho ({cropped.shape[0]}px). Usando fallback clásico.")
            else:
                return cropped
                
        # 5. Fallback clásico (si no encuentra suficientes números claros)
        target_y = -1
        for i, text in enumerate(data['text']):
            t = text.lower()
            if 'instal' in t or 'install' in t or 'proporcione' in t:
                y_bottom = data['top'][i] + data['height'][i]
                if y_bottom > target_y:
                    target_y = y_bottom
                    
        if target_y != -1:
            margin = max(int(h * 0.35), 70)
            crop_end = min(h, target_y + margin)
            cropped = gray[target_y:crop_end, :]
            if cropped.shape[0] >= 30:
                return cropped
                
    except Exception as e:
        logger.error(f"Error en crop de banda horizontal: {e}")
        
    return gray

def process_image(image_bytes: bytes, rescue: bool = False, skip_crop: bool = False):
    try:
        t_start_total = time.perf_counter()
        
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("No se pudo decodificar la imagen")

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        t0 = time.perf_counter()
        
        # 1. CROP GEOMÉTRICO
        if not skip_crop:
            gray = crop_to_iid_region(gray)
            logger.info(f"[OCR] Crop geométrico aplicado: {time.perf_counter() - t0:.3f}s. Dimensiones: {gray.shape}")
        else:
            logger.info(f"[OCR] Omitiendo recálculo de crop en modo rescate. Dimensiones: {gray.shape}")
        
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

        if not rescue:
            # Fast Path: Banda horizontal única, por tanto PSM 7 es ideal. Solo números.
            custom_config_whitelist = r'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789 \n\r\t-'
        else:
            # Rescue Path: Puede haber ruido o texto múltiple. Permitimos PSM 11 y letras.
            custom_config_whitelist = r'--oem 3 --psm 11 -c tessedit_char_whitelist=0123456789OQILJZS$GTYB \n\r\t-'
        
        # Estructura para votación por bloques: lista de 9 diccionarios (conteo de votos)
        block_votes = [{} for _ in range(9)]
        
        # Candidatos completos encontrados
        all_candidates = []
        
        for name, processed_img in strategies:
            t0 = time.perf_counter()
            text = pytesseract.image_to_string(processed_img, config=custom_config_whitelist)
            logger.debug(f"[OCR] [{name}] Texto bruto extraído:\n{text}")
            
            # Separar por espacios, tabulaciones o saltos de línea (Tesseract suele usar espacios entre bloques)
            raw_tokens = re.split(r'[\s\-]+', text.upper())
            
            # Normalizar cada token
            tokens = [normalize_block(t) for t in raw_tokens if t]
            
            # Buscar una secuencia de 9 tokens de 7 dígitos consecutivos
            found_sequence = False
            for i in range(len(tokens) - 8):
                sequence = tokens[i:i+9]
                if all(len(tk) == 7 for tk in sequence):
                    found_sequence = True
                    iid = "".join(sequence)
                    all_candidates.append({"iid": iid, "method": "perfect-9x7", "strategy": name, "score": 100})
                    # Registrar votos individuales por bloque
                    for b_idx, tk in enumerate(sequence):
                        block_votes[b_idx][tk] = block_votes[b_idx].get(tk, 0) + 1
                    break
                    
            if not found_sequence:
                # Fallback estricto: Unir todo y buscar 63 dígitos seguidos (por si no separó bien con espacios)
                merged = "".join(tokens)
                if len(merged) >= 63:
                    # Extraemos los primeros 63 y los dividimos en bloques de 7 para votar
                    iid = merged[:63]
                    all_candidates.append({"iid": iid, "method": "exact-63-merged", "strategy": name, "score": 80})
                    for b_idx in range(9):
                        tk = iid[b_idx*7:(b_idx+1)*7]
                        block_votes[b_idx][tk] = block_votes[b_idx].get(tk, 0) + 1
                        
            logger.info(f"[OCR] {name}: {time.perf_counter() - t0:.3f}s")

        if not all_candidates:
            if not rescue:
                # Fallback automático: si Fast Path no encuentra absolutamente nada, probamos Rescue directamente
                logger.info("[OCR] Fast Path no encontró nada. Activando Auto-Rescue.")
                # Aquí NO le pasamos skip_crop=True intencionalmente si falló rotundamente, porque 
                # a lo mejor el crop anterior falló. Enviamos rescue=True y dejamos que reintente a su manera.
                return process_image(image_bytes, rescue=True)
            return {"success": False, "error": "No se pudo encontrar un IID válido en la imagen"}
            
        # Deduplicar preservando el mejor score y otorgando bonus por CONSENSO
        # Construir el candidato Frankenstein basado en el consenso de bloques
        frankenstein_blocks = []
        frankenstein_confidence = 0
        for b_votes in block_votes:
            if b_votes:
                # Elegir el bloque con más votos
                best_tk = max(b_votes, key=b_votes.get)
                frankenstein_blocks.append(best_tk)
                frankenstein_confidence += b_votes[best_tk]
            else:
                frankenstein_blocks.append("0000000") # Relleno si no hay nada
                
        if len(frankenstein_blocks) == 9:
            frank_iid = "".join(frankenstein_blocks)
            # Agregar el Frankenstein como candidato si no existe
            if not any(c["iid"] == frank_iid for c in all_candidates):
                all_candidates.append({"iid": frank_iid, "method": "frankenstein-consensus", "strategy": "Consensus", "score": 100 + frankenstein_confidence})
            else:
                for c in all_candidates:
                    if c["iid"] == frank_iid:
                        c["score"] += frankenstein_confidence * 5 # Súper boost

        unique_candidates = {}
        for c in all_candidates:
            iid = c["iid"]
            if iid not in unique_candidates:
                unique_candidates[iid] = c
                unique_candidates[iid]["votes"] = 1
            else:
                unique_candidates[iid]["score"] = max(unique_candidates[iid]["score"], c["score"])
                unique_candidates[iid]["votes"] += 1
                
        for iid in unique_candidates:
            if unique_candidates[iid]["votes"] > 1:
                unique_candidates[iid]["score"] += (unique_candidates[iid]["votes"] - 1) * 20
                
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
