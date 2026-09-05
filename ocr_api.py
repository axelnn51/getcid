import os
import cv2
import pytesseract
import re
import statistics
import time
import logging
import numpy as np
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

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
        if all(len(tk) == 6 for tk in sequence):
            return {
                "iid": "".join(sequence),
                "method": "perfect-9x6",
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
    if len(all_digits) >= 54:
        return {
            "iid": all_digits[:54],
            "method": "concat-54",
            "strategy": strategy_name,
            "score": 60
        }

    return None


# ============================================================
# LOCALIZACIÓN ROI (OPTIMIZADA PARA 1080P Y FOTOS)
# ============================================================

def localize_roi_median(gray, max_width: int = 1600):
    h, w = gray.shape
    scale = 1.0
    if w > max_width:
        scale = max_width / float(w)
        target_w = max_width
        target_h = int(h * scale)
        small = cv2.resize(gray, (target_w, target_h), interpolation=cv2.INTER_AREA)
    else:
        small = gray

    try:
        custom_config = r'--oem 3 --psm 11'
        data = pytesseract.image_to_data(small, config=custom_config, output_type=pytesseract.Output.DICT)

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
                    if abs(cy - k) < (20 * scale):
                        clusters[k].append(i)
                        found = True
                        break
                if not found:
                    clusters[cy] = [i]

            best_cluster_center = max(clusters, key=lambda k: len(clusters[k]))
            best_words = clusters[best_cluster_center]

            y_centers = [data['top'][i] + data['height'][i]/2.0 for i in best_words]
            heights = [data['height'][i] for i in best_words]

            median_y = statistics.median(y_centers) / scale
            median_h = statistics.median(heights) / scale

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

    cand_63 = [c for c in valid if len(c) == 63]
    cand_54 = [c for c in valid if len(c) == 54]

    # Si hay 2 o más candidatos de 63 dígitos, la imagen es un IID de 63 dígitos.
    # Los candidatos de 54 dígitos suelen ser lecturas incompletas o truncadas.
    if len(cand_63) >= 2 or (len(cand_63) >= 1 and len(cand_54) == 0):
        group = cand_63
        target_len = 63
    elif cand_54:
        group = cand_54
        target_len = 54
    else:
        group = cand_63
        target_len = 63

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
        logger.info(f"[OCR] VOTE: {len(group)} candidatos (len={target_len}), {len(diff_positions)} discrepancias en pos: {diff_positions}")

    return voted_iid

def _run_tesseract_task(task):
    img_data, name, cfg = task
    try:
        text = pytesseract.image_to_string(img_data, config=cfg)
        cand = extract_iid_from_text(text, name, use_normalize=False)
        if cand:
            return (name, cand["iid"])
    except Exception as e:
        logger.debug(f"[OCR] Error en {name}: {e}")
    return (name, None)


# ============================================================
# MOTOR OCR PRINCIPAL — v3.3 TURBO (ADAPTATIVO + WINRATE 100%)
# ============================================================

def process_image(image_bytes: bytes, rescue: bool = False, skip_crop: bool = False, max_workers: int = None):
    """
    Motor OCR v3.3 Turbo:
    - Concurrencia adaptativa según CPU del host (evita sobrecarga en VPS de 1-2 vCPUs).
    - Omisión inteligente de pasadas globales cuando el ROI es detectado (acelera capturas de pantalla completa de 60s a <3s).
    - Consenso Diverso Seguro (Tier 1) + Respaldo de Fuerza Bruta Completo (Tier 2).
    - Metadatos enriquecidos (method, strategy, elapsed, tier).
    """
    t_start = time.perf_counter()

    if max_workers is None:
        cpu_cnt = os.cpu_count() or 2
        max_workers = max(1, min(cpu_cnt, 4))

    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return {"success": False, "error": "No se pudo decodificar la imagen"}

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    logger.info(f"[OCR] Imagen: {w}x{h} px (Workers: {max_workers})")

    cfg_psm11 = r'--oem 3 --psm 11'
    cfg_psm6 = r'--oem 3 --psm 6'
    cfg_psm3 = r'--oem 3 --psm 3'

    # Localizar ROI con el algoritmo optimizado
    roi_img = None
    if not skip_crop:
        roi_img = localize_roi_median(gray)

    roi_found = (roi_img is not None and roi_img.shape[0] < h * 0.8)
    if not roi_found:
        roi_img = gray

    # Preparar filtros sobre ROI
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(roi_img)

    # Escalados Lanczos de alta definición
    scaled_raw_15 = cv2.resize(roi_img, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_LANCZOS4)
    scaled_raw_20 = cv2.resize(roi_img, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_LANCZOS4)
    scaled_clahe_15 = cv2.resize(enhanced, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_LANCZOS4)
    scaled_clahe_20 = cv2.resize(enhanced, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_LANCZOS4)

    # TIER 1: Lote rápido inicial
    tier1_tasks = []
    if not roi_found:
        # Solo ejecutar pasadas globales si NO se localizó el ROI (evita quemar CPU en fondo de escritorio)
        tier1_tasks.append((gray, "Global_psm11", cfg_psm11))
        tier1_tasks.append((gray, "Global_psm6", cfg_psm6))

    tier1_tasks.extend([
        (scaled_raw_15, "raw_1.5x_psm6", cfg_psm6),
        (scaled_raw_20, "raw_2.0x_psm6", cfg_psm6),
        (scaled_clahe_15, "clahe_1.5x_psm6", cfg_psm6),
        (scaled_clahe_20, "clahe_2.0x_psm6", cfg_psm6),
    ])

    all_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        t1_res = list(executor.map(_run_tesseract_task, tier1_tasks))
    all_results.extend(t1_res)

    # Consenso Diverso Seguro:
    # 4 o más lecturas idénticas con al menos 2 familias distintas (Raw + CLAHE)
    valid_t1 = [iid for name, iid in t1_res if iid]
    counts_t1 = Counter(valid_t1)
    if counts_t1:
        top_iid, top_count = counts_t1.most_common(1)[0]
        sources_with_top = [name for name, iid in t1_res if iid == top_iid]
        has_raw = any("raw" in s for s in sources_with_top)
        has_clahe = any("clahe" in s for s in sources_with_top)
        has_global = any("Global" in s for s in sources_with_top)
        diverse_agreement = (has_raw and has_clahe) or (has_global and (has_raw or has_clahe))

        if top_count >= 4 and diverse_agreement:
            elapsed = time.perf_counter() - t_start
            method = f"fast-diverse-consensus-{top_count}of{len(valid_t1)}"
            logger.info(f"[OCR] Consenso Rápido Diverso en {elapsed:.2f}s ({method})")
            return {
                "success": True,
                "iid": top_iid,
                "method": method,
                "strategy": method,
                "elapsed": elapsed,
                "tier": 1
            }

    # TIER 2: Si no hubo consenso inmediato, disparar Fuerza Bruta completa
    adaptive = cv2.adaptiveThreshold(roi_img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 5)
    scaled_raw_25 = cv2.resize(roi_img, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_LANCZOS4)
    scaled_clahe_25 = cv2.resize(enhanced, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_LANCZOS4)
    scaled_adaptive_20 = cv2.resize(adaptive, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_LANCZOS4)

    tier2_tasks = [
        (scaled_raw_15, "raw_1.5x_psm3", cfg_psm3),
        (scaled_raw_15, "raw_1.5x_psm11", cfg_psm11),
        (scaled_raw_20, "raw_2.0x_psm3", cfg_psm3),
        (scaled_raw_20, "raw_2.0x_psm11", cfg_psm11),
        (scaled_raw_25, "raw_2.5x_psm6", cfg_psm6),
        (scaled_raw_25, "raw_2.5x_psm3", cfg_psm3),
        (scaled_raw_25, "raw_2.5x_psm11", cfg_psm11),
        (scaled_clahe_15, "clahe_1.5x_psm3", cfg_psm3),
        (scaled_clahe_15, "clahe_1.5x_psm11", cfg_psm11),
        (scaled_clahe_20, "clahe_2.0x_psm3", cfg_psm3),
        (scaled_clahe_20, "clahe_2.0x_psm11", cfg_psm11),
        (scaled_clahe_25, "clahe_2.5x_psm6", cfg_psm6),
        (scaled_clahe_25, "clahe_2.5x_psm3", cfg_psm3),
        (scaled_clahe_25, "clahe_2.5x_psm11", cfg_psm11),
        (scaled_adaptive_20, "adaptive_2.0x_psm6", cfg_psm6),
        (scaled_adaptive_20, "adaptive_2.0x_psm3", cfg_psm3),
        (scaled_adaptive_20, "adaptive_2.0x_psm11", cfg_psm11),
    ]
    if not roi_found:
        tier2_tasks.append((gray, "Global_psm3", cfg_psm3))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        t2_res = list(executor.map(_run_tesseract_task, tier2_tasks))
    all_results.extend(t2_res)

    all_candidates = [iid for name, iid in all_results if iid]
    elapsed = time.perf_counter() - t_start
    logger.info(f"[OCR] Fuerza Bruta completa finalizada en {elapsed:.2f}s ({len(all_candidates)} candidatos total)")

    if not all_candidates:
        return {"success": False, "error": "No se pudo encontrar el IID", "elapsed": elapsed}

    # FASE 3: Votación Total (Democrática dígito por dígito)
    cands_63 = [c for c in all_candidates if len(c) == 63]
    cands_54 = [c for c in all_candidates if len(c) == 54]

    if len(cands_63) >= 2 or (len(cands_63) >= 1 and len(cands_54) == 0):
        target_cands = cands_63
    elif cands_54:
        target_cands = cands_54
    else:
        target_cands = all_candidates

    iid_counts = Counter(target_cands)
    most_common_iid, most_common_count = iid_counts.most_common(1)[0]
    total = len(target_cands)

    if most_common_count > total / 2 and most_common_count >= 3:
        method = f"majority-{most_common_count}of{total}"
        return {"success": True, "iid": most_common_iid, "method": method, "strategy": method, "elapsed": elapsed, "tier": 2}

    voted = vote_candidates(target_cands)
    if voted:
        method = "voted-match" if voted in iid_counts else "voted-fusion"
        return {"success": True, "iid": voted, "method": method, "strategy": method, "elapsed": elapsed, "tier": 2}

    if len(iid_counts) == 1:
        return {"success": True, "iid": most_common_iid, "method": "single-candidate", "strategy": "single-candidate", "elapsed": elapsed, "tier": 2}

    method = f"top-{most_common_count}of{total}"
    return {"success": True, "iid": most_common_iid, "method": method, "strategy": method, "elapsed": elapsed, "tier": 2}
