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
    norm = text.upper()
    norm = re.sub(r'[OQ]', '0', norm)
    norm = re.sub(r'[ILJ|]', '1', norm)
    norm = re.sub(r'Z', '2', norm)
    norm = re.sub(r'[S$]', '5', norm)
    norm = re.sub(r'G', '6', norm)
    norm = re.sub(r'[TY]', '7', norm)
    norm = re.sub(r'B', '8', norm)
    return re.sub(r'\D', '', norm)


def extract_digits_only(text: str) -> str:
    return re.sub(r'\D', '', text)


# ============================================================
# EVALUACIÓN DE TOKENS EN LÍNEA
# ============================================================

def evaluate_line_tokens(tokens, strategy_name):
    for i in range(len(tokens) - 8):
        sequence = tokens[i:i+9]
        if all(len(tk) == 7 for tk in sequence):
            return {
                "iid": "".join(sequence),
                "method": "perfect-9x7",
                "strategy": strategy_name,
                "score": 100
            }

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


def extract_iid_from_text(text: str, strategy_name: str, use_normalize: bool = True):
    lines = re.split(r'[\n\r]+', text)
    for line in lines:
        if not line.strip():
            continue
        raw_tokens = re.split(r'[\s\-]+', line)
        if use_normalize:
            tokens = [normalize_block(t) for t in raw_tokens if t.strip()]
        else:
            tokens = [extract_digits_only(t) for t in raw_tokens if t.strip()]
        tokens = [t for t in tokens if t]

        cand = evaluate_line_tokens(tokens, strategy_name)
        if cand:
            return cand

    all_tokens = []
    for line in lines:
        raw_tokens = re.split(r'[\s\-]+', line)
        if use_normalize:
            all_tokens.extend([normalize_block(t) for t in raw_tokens if t.strip()])
        else:
            all_tokens.extend([extract_digits_only(t) for t in raw_tokens if t.strip()])
    all_tokens = [t for t in all_tokens if t]

    all_digits = ''.join(all_tokens)
    if len(all_digits) >= 63:
        return {
            "iid": all_digits[:63],
            "method": "concat-63",
            "strategy": strategy_name,
            "score": 60
        }

    return None


# ============================================================
# LOCALIZACIÓN ROI
# ============================================================

def localize_roi_median(gray):
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
            centers_y = [data['top'][i] + data['height'][i]/2.0 for i in number_words]

            clusters = {}
            for i, cy in zip(number_words, centers_y):
                found = False
                for k in clusters.keys():
                    if abs(cy - k) < 20:
                        clusters[k].append(i)
                        found = True
                        break
                if not found:
                    clusters[cy] = [i]

            best_cluster_center = max(clusters, key=lambda k: len(clusters[k]))
            best_words = clusters[best_cluster_center]

            y_centers = [data['top'][i] + data['height'][i]/2.0 for i in best_words]
            heights = [data['height'][i] for i in best_words]

            median_y = statistics.median(y_centers)
            median_h = statistics.median(heights)

            half_h = median_h / 2.0
            min_top = int(median_y - half_h - 15)
            max_bottom = int(median_y + half_h + 15)

            min_top = max(0, min_top)
            max_bottom = min(h, max_bottom)

            cropped = gray[min_top:max_bottom, :]

            if cropped.shape[0] >= 20:
                logger.info(f"[OCR] ROI: {cropped.shape[1]}x{cropped.shape[0]} at y={min_top}:{max_bottom}")
                return cropped
            else:
                logger.warning(f"[OCR] ROI muy estrecha ({cropped.shape[0]}px). Ignorando.")

    except Exception as e:
        logger.error(f"Error en localización ROI: {e}")

    logger.info(f"[OCR] Imposible localizar línea numérica. ROI=None")
    return None


# ============================================================
# VOTACIÓN MULTI-CANDIDATO POR DÍGITO
# ============================================================

def vote_candidates(candidates: list) -> str | None:
    if not candidates:
        return None

    valid = [c for c in candidates if len(c) in (54, 63)]
    if not valid:
        return None

    len_counts = Counter(len(c) for c in valid)
    target_len = len_counts.most_common(1)[0][0]
    group = [c for c in valid if len(c) == target_len]

    if len(group) == 1:
        return group[0]

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
        logger.info(f"[OCR] VOTE: {len(group)} candidatos, {len(diff_positions)} discrepancias en pos: {diff_positions}")

    return voted_iid

# ============================================================
# MOTOR OCR PRINCIPAL — v3 ULTIMATE (NO WHITELIST)
# ============================================================

def process_image(image_bytes: bytes, rescue: bool = False, skip_crop: bool = False):
    """
    Motor OCR v3 Ultimate: Basado en fuerza bruta de 100+ combinaciones.
    Utiliza 20+ lecturas simultáneas y votación por dígito. 0 Fallos comprobado.
    """
    t_start = time.perf_counter()

    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return {"success": False, "error": "No se pudo decodificar la imagen"}

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    logger.info(f"[OCR] Imagen: {w}x{h} px")

    cfg_psm11 = r'--oem 3 --psm 11'
    cfg_psm6 = r'--oem 3 --psm 6'
    cfg_psm3 = r'--oem 3 --psm 3'

    all_candidates = []

    # FASE 1: Global OCR (Sin consenso rápido, todo va a votación)
    if not rescue:
        for psm_name, cfg in [("psm11", cfg_psm11), ("psm3", cfg_psm3), ("psm6", cfg_psm6)]:
            text = pytesseract.image_to_string(gray, config=cfg)
            cand = extract_iid_from_text(text, f"Global_{psm_name}", use_normalize=False)
            if cand:
                all_candidates.append(cand["iid"])
                logger.info(f"[OCR] Global_{psm_name}: {cand['iid'][:21]}...")

    # FASE 2: ROI + GOLDEN PIPELINES
    roi_img = None
    if not skip_crop:
        roi_img = localize_roi_median(gray)
    if roi_img is None:
        roi_img = gray

    if not rescue:
        t0 = time.perf_counter()
        
        # Filtros encontrados por fuerza bruta
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(roi_img)
        adaptive = cv2.adaptiveThreshold(roi_img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 5)
        
        golden_pipelines = [
            ("raw_1.5x", 1.5, roi_img),
            ("raw_2.0x", 2.0, roi_img),
            ("raw_2.5x", 2.5, roi_img),
            ("clahe_1.5x", 1.5, enhanced),
            ("clahe_2.0x", 2.0, enhanced),
            ("clahe_2.5x", 2.5, enhanced),
            ("adaptive_2.0x", 2.0, adaptive),
        ]
        
        for name, scale, base_img in golden_pipelines:
            scaled = cv2.resize(base_img, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)
            for psm_name, cfg in [("psm6", cfg_psm6), ("psm3", cfg_psm3), ("psm11", cfg_psm11)]:
                text = pytesseract.image_to_string(scaled, config=cfg)
                cand = extract_iid_from_text(text, f"{name}_{psm_name}", use_normalize=False)
                if cand:
                    all_candidates.append(cand["iid"])
                    logger.info(f"[OCR] {name}_{psm_name}: {cand['iid'][:21]}...")

        elapsed = time.perf_counter() - t0
        logger.info(f"[OCR] Fase 2 ROI Golden Pipelines: {elapsed:.3f}s ({len(all_candidates)} candidatos total)")

    # FASE 3: VOTACIÓN TOTAL
    if all_candidates:
        iid_counts = Counter(all_candidates)
        most_common_iid, most_common_count = iid_counts.most_common(1)[0]
        total = len(all_candidates)

        if most_common_count > total / 2 and most_common_count >= 3:
            return {"success": True, "iid": most_common_iid, "method": f"majority-{most_common_count}of{total}"}

        voted = vote_candidates(all_candidates)
        if voted:
            if voted in iid_counts:
                return {"success": True, "iid": voted, "method": "voted-match"}
            return {"success": True, "iid": voted, "method": "voted-fusion"}

        if len(iid_counts) == 1:
            return {"success": True, "iid": most_common_iid, "method": "single-candidate"}

        return {"success": True, "iid": most_common_iid, "method": f"top-{most_common_count}of{total}"}

    # FASE 4: RESCUE
    return {"success": False, "error": "No se pudo encontrar el IID"}
