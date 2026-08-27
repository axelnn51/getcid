// ============================================================
// Motor OCR con Worker Persistente
// Carga el modelo UNA sola vez y lo reutiliza para siempre
// Ahora retorna dígitos parciales en errores para diagnóstico
// ============================================================
const Tesseract = require('tesseract.js');
const sharp = require('sharp');
const fs = require('fs');

let worker = null;
let workerReady = false;

// Inicializar worker al arrancar (solo una vez)
async function initWorker() {
    if (workerReady) return;
    console.log('[OCR] 🧠 Cargando motor OCR (solo una vez)...');
    const start = Date.now();
    worker = await Tesseract.createWorker('eng');
    
    // 🔥 OPTIMIZACIÓN: Restringir caracteres a números y letras comunes confundidas
    // Esto acelera el reconocimiento y mejora la precisión
    await worker.setParameters({
        tessedit_char_whitelist: '0123456789OQILJZS$GTYB \n\r-:',
    });
    
    workerReady = true;
    console.log(`[OCR] 🧠 Motor OCR listo en ${Date.now() - start}ms`);
}

// Estrategias de preprocesamiento (Dimensiones reducidas para máxima velocidad)
// Ordenadas por tasa de éxito empírica en Telegram (fotos comprimidas/pantallas)
const STRATEGIES = [
    { name: "Blurry", process: (i, o) => sharp(i).resize({ width: 1500, withoutEnlargement: true }).greyscale().normalize().sharpen({ sigma: 2 }).toFile(o) },
    { name: "Photo", process: (i, o) => sharp(i).resize({ width: 1200, withoutEnlargement: true }).greyscale().median(3).linear(1.5, 0).sharpen().toFile(o) },
    { name: "Screenshot", process: (i, o) => sharp(i).resize({ width: 1200, withoutEnlargement: true }).greyscale().sharpen().toFile(o) },
    { name: "Binary", process: (i, o) => sharp(i).resize({ width: 1500, withoutEnlargement: true }).greyscale().linear(2.0, -0.3).threshold(140).toFile(o) },
    { name: "LCD_Screen", process: (i, o) => sharp(i).resize({ width: 1500, withoutEnlargement: true }).greyscale().blur(1.5).normalize().linear(1.8, -0.2).sharpen().toFile(o) },
    { name: "LCD_Aggressive", process: (i, o) => sharp(i).resize({ width: 1200, withoutEnlargement: true }).greyscale().blur(2.0).threshold(128).toFile(o) }
];

// Extraer IID del texto OCR
function extractIID(rawText) {
    // Directo: 9 bloques de 7 dígitos
    const d7 = rawText.match(/\b\d{7}\b/g);
    if (d7 && d7.length >= 9) { const iid = d7.slice(-9).join(''); if (iid.length === 63) return { iid, method: 'direct' }; }

    // Normalizado: corregir letras confundidas
    const norm = rawText.toUpperCase()
        .replace(/[OQ]/g, '0').replace(/[ILJ|]/g, '1').replace(/Z/g, '2')
        .replace(/[S$]/g, '5').replace(/G/g, '6').replace(/[TY]/g, '7').replace(/B/g, '8');
    const n7 = norm.match(/\b\d{7}\b/g);
    if (n7 && n7.length >= 9) { const iid = n7.slice(-9).join(''); if (iid.length === 63) return { iid, method: 'normalized' }; }

    // Word scan
    const words = norm.split(/[\s\n\r,;:]+/);
    let sevens = [];
    for (const w of words) { const d = w.replace(/\D/g, ''); if (d.length === 7) sevens.push(d); }
    if (sevens.length >= 9) return { iid: sevens.slice(-9).join(''), method: 'word-scan' };

    // Fallback
    const all = norm.replace(/\D/g, '');
    if (all.length >= 63) return { iid: all.slice(-63), method: 'fallback' };

    // Retornar dígitos parciales para diagnóstico (si hay al menos algo)
    if (all.length > 0) return { iid: all, method: 'partial', partial: true };

    return null;
}

// Limpiar archivo temporal de forma segura
function safeUnlink(filePath) {
    try { if (fs.existsSync(filePath)) fs.unlinkSync(filePath); } catch(e) { /* ignore */ }
}

// Procesar imagen con OCR y obtener CID
async function ocrAndGetCID(filePath, getCID) {
    if (!workerReady) await initWorker();

    let lastPartialIID = null;
    let lastError = null;

    for (let i = 0; i < STRATEGIES.length; i++) {
        const s = STRATEGIES[i];
        const processed = filePath + `_s${i}.png`;
        try {
            await s.process(filePath, processed);
            const { data: { text } } = await worker.recognize(processed);
            safeUnlink(processed);

            const result = extractIID(text);
            if (result) {
                if (result.partial) {
                    // Guardar parcial para mostrar en error
                    lastPartialIID = result.iid;
                    continue;
                }
                try {
                    const cidResult = await getCID(result.iid);
                    const cidVal = typeof cidResult === 'string' ? cidResult : cidResult.cid;
                    const backendMethod = typeof cidResult === 'object' ? cidResult.method : 'unknown';
                    return { success: true, iid: result.iid, cid: cidVal, strategy: s.name, method: result.method, backendMethod };
                } catch (e) {
                    lastError = e;
                    lastPartialIID = result.iid;
                    // Si es checksum inválido, intentar siguiente estrategia
                    if (e.code === 'INVALID_CHECKSUM' || e.message === 'INVALID_CHECKSUM') continue;
                    // Para otros errores de Microsoft, no reintentar OCR (el IID está correcto)
                    if (e.code && e.code.startsWith('MS_') || e.code === 'KEY_BLOCKED' || 
                        e.code === 'TOO_MANY_ACTIVATIONS' || e.code === 'ACTIVATION_FAILED' ||
                        e.code === 'KEY_EXPIRED' || e.code === 'KEY_NOT_GENUINE' ||
                        e.code === 'INVALID_PRODUCT' || e.code === 'TIMEOUT' ||
                        e.code === 'NO_CID_IN_RESPONSE' || e.code === 'NETWORK_ERROR') {
                        // Error de Microsoft, no de OCR — propagar con IID
                        e.iid = result.iid;
                        throw e;
                    }
                    continue;
                }
            }
        } catch (e) {
            safeUnlink(processed);
            // Si es un error de Microsoft (no de OCR), propagarlo
            if (e.code && e.code !== 'INVALID_CHECKSUM') throw e;
            if (e.message !== 'INVALID_CHECKSUM') lastError = e;
        }
    }

    // Todas las estrategias fallaron
    return { 
        success: false, 
        detectedDigits: lastPartialIID || null,
        lastError: lastError ? (lastError.code || lastError.message) : null
    };
}

// Solo extraer IID de imagen (sin pedir CID)
async function ocrExtractOnly(filePath) {
    if (!workerReady) await initWorker();

    for (let i = 0; i < STRATEGIES.length; i++) {
        const s = STRATEGIES[i];
        const processed = filePath + `_ocr${i}.png`;
        try {
            await s.process(filePath, processed);
            const { data: { text } } = await worker.recognize(processed);
            safeUnlink(processed);

            const result = extractIID(text);
            if (result && !result.partial) return { success: true, iid: result.iid, method: result.method, strategy: s.name };
        } catch (e) {
            safeUnlink(processed);
        }
    }
    return { success: false };
}

module.exports = { initWorker, extractIID, ocrAndGetCID, ocrExtractOnly, STRATEGIES };
