// ============================================================
// Motor OCR con Worker Persistente
// Carga el modelo UNA sola vez y lo reutiliza para siempre
// ============================================================
const Tesseract = require('tesseract.js');
const sharp = require('sharp');
const fs = require('fs');

let worker = null;
let workerReady = false;

// Inicializar worker al arrancar (solo una vez)
async function initWorker() {
    if (workerReady) return;
    console.log('🧠 Cargando motor OCR (solo una vez)...');
    const start = Date.now();
    worker = await Tesseract.createWorker('eng');
    workerReady = true;
    console.log(`🧠 Motor OCR listo en ${Date.now() - start}ms`);
}

// Estrategias de preprocesamiento
const STRATEGIES = [
    { name: "Screenshot", process: (i, o) => sharp(i).greyscale().sharpen().toFile(o) },
    { name: "Photo", process: (i, o) => sharp(i).resize({ width: 2000 }).greyscale().median(3).linear(1.5, 0).sharpen().toFile(o) },
    { name: "Blurry", process: (i, o) => sharp(i).resize({ width: 3000 }).greyscale().normalize().sharpen({ sigma: 2 }).toFile(o) },
    { name: "Binary", process: (i, o) => sharp(i).resize({ width: 2500 }).greyscale().linear(2.0, -0.3).threshold(140).toFile(o) }
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
    return null;
}

// Procesar imagen con OCR y obtener CID
async function ocrAndGetCID(filePath, getCID) {
    if (!workerReady) await initWorker();

    for (let i = 0; i < STRATEGIES.length; i++) {
        const s = STRATEGIES[i];
        const processed = filePath + `_s${i}.png`;
        try {
            await s.process(filePath, processed);
            const { data: { text } } = await worker.recognize(processed);
            if (fs.existsSync(processed)) fs.unlinkSync(processed);

            const result = extractIID(text);
            if (result) {
                const cid = await getCID(result.iid);
                return { success: true, iid: result.iid, cid, strategy: s.name, method: result.method };
            }
        } catch (e) {
            if (fs.existsSync(processed)) fs.unlinkSync(processed);
            if (e.message === 'INVALID_CHECKSUM') continue;
        }
    }
    return { success: false };
}

module.exports = { initWorker, extractIID, ocrAndGetCID, STRATEGIES };
