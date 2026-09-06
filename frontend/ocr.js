// ============================================================
// Motor OCR Delegado (Backend Python)
// ============================================================
const fs = require('fs');
const FormData = require('form-data');

// Leer la URL del backend desde el .env
require('dotenv').config();
const GETCID_SERVICE_URL = process.env.GETCID_SERVICE_URL || 'http://getcid_backend:8000';

async function initWorker() {
    // Ya no es necesario inicializar Tesseract localmente
    console.log('[OCR] 🧠 Usando motor OCR en Backend (Python)...');
}

// Limpiar archivo temporal de forma segura
function safeUnlink(filePath) {
    try { if (fs.existsSync(filePath)) fs.unlinkSync(filePath); } catch(e) { /* ignore */ }
}

async function extractFromBackend(filePath, rescue = false) {
    try {
        const form = new FormData();
        form.append('file', fs.createReadStream(filePath));
        form.append('rescue', rescue ? 'true' : 'false');

        // fetch nativo en Node 18+ (FormData no es totalmente nativo en node fetch antiguo, 
        // pero usaremos node-fetch o el fetch global con el form de form-data)
        const fetch = require('node-fetch'); // Asumiendo que node-fetch está en package.json
        const response = await fetch(`${GETCID_SERVICE_URL}/extract-iid`, {
            method: 'POST',
            body: form
        });
        
        if (!response.ok) {
            console.error(`[OCR] Error HTTP del backend: ${response.status}`);
            return { success: false, error: `HTTP ${response.status}` };
        }
        
        const data = await response.json();
        return data;
    } catch (e) {
        console.error(`[OCR] Error conectando al backend: ${e.message}`);
        return { success: false, error: e.message };
    }
}

// Procesar imagen con OCR y obtener CID
async function ocrAndGetCID(filePath, getCID, onProgress = null) {
    if (typeof onProgress === 'function') {
        onProgress('ocr_start', {});
    }

    const ocrResult = await extractFromBackend(filePath);
    
    if (!ocrResult || !ocrResult.success) {
        return { 
            success: false, 
            detectedDigits: ocrResult ? ocrResult.detected_digits : null,
            lastError: ocrResult ? ocrResult.error : 'Error desconocido de OCR'
        };
    }

    const ocrElapsed = typeof ocrResult.elapsed === 'number' ? ocrResult.elapsed : null;

    let candidates = ocrResult.candidates || [{ 
        iid: ocrResult.iid, 
        strategy: ocrResult.strategy || ocrResult.method || 'v3.4-fast', 
        method: ocrResult.method || 'v3.4-fast' 
    }];
    let rescueAttempted = ocrResult.rescue_mode === true || ocrResult.tier === 2 || (ocrResult.candidates && ocrResult.candidates.length > 1);
    
    if (typeof onProgress === 'function') {
        onProgress('ocr_done', {
            iid: ocrResult.iid,
            candidatesCount: candidates.length,
            ocrElapsed: ocrElapsed,
            strategy: ocrResult.strategy || ocrResult.method
        });
    }

    let lastErrorObj = null;
    let lastPartialIID = null;

    for (let i = 0; i < candidates.length; i++) {
        const cand = candidates[i];
        const iid = cand.iid;
        const strategy = cand.strategy || cand.method || 'v3.3-turbo';
        const method = cand.method || 'v3.3-turbo';
        
        console.log(`[OCR] Probando candidato IID (${i+1}/${candidates.length}): ${iid} (Score: ${cand.score || '?'})`);

        if (typeof onProgress === 'function') {
            onProgress('calling_cid', {
                iid: iid,
                candidateIndex: i + 1,
                totalCandidates: candidates.length
            });
        }

        const cidStartTime = Date.now();
        try {
            const cidResult = await getCID(iid);
            const cidVal = typeof cidResult === 'string' ? cidResult : cidResult.cid;
            const backendMethod = typeof cidResult === 'object' ? cidResult.method : 'unknown';
            const cidElapsed = (Date.now() - cidStartTime) / 1000;
            
            return { 
                success: true, 
                iid: iid, 
                cid: cidVal, 
                strategy: strategy, 
                method: method, 
                backendMethod,
                ocrElapsed: ocrElapsed,
                cidElapsed: cidElapsed
            };
        } catch (e) {
            // Error de Microsoft o de CID
            const code = e.code || '';
            const msg = e.message || '';
            
            if (code.startsWith('MS_') || code === 'KEY_BLOCKED' || 
                code === 'TOO_MANY_ACTIVATIONS' || code === 'ACTIVATION_FAILED' ||
                code === 'KEY_EXPIRED' || code === 'KEY_NOT_GENUINE' ||
                code === 'INVALID_PRODUCT' || code === 'TIMEOUT' ||
                code === 'NO_CID_IN_RESPONSE' || code === 'NETWORK_ERROR') {
                
                // Error fatal de MS, no tiene sentido seguir intentando otros IID
                e.iid = iid;
                throw e;
            }
            
            if (code === 'INVALID_CHECKSUM' || msg === 'INVALID_CHECKSUM' || msg.includes('checksum') || msg.includes('inválido')) {
                console.log(`[OCR] Candidato falló por checksum. Intentando siguiente...`);
                lastErrorObj = e;
                lastPartialIID = iid;
                continue; // Probar siguiente candidato
            }
            
            // Otro error
            lastErrorObj = e;
            lastPartialIID = iid;
        }
    }
    
    // Si todos los candidatos fallaron por checksum y aún no hemos intentado el modo rescate explícito
    if (lastErrorObj && (lastErrorObj.code === 'INVALID_CHECKSUM' || lastErrorObj.message === 'INVALID_CHECKSUM' || lastErrorObj.message.includes('checksum'))) {
        if (!rescueAttempted) {
            console.log(`[OCR] FAST PATH falló. Activando RESCUE MODE (solicitando más candidatos)...`);
            if (typeof onProgress === 'function') {
                onProgress('rescue_mode', { lastIID: lastPartialIID });
            }
            const rescueResult = await extractFromBackend(filePath, true);
            
            if (rescueResult && rescueResult.success && rescueResult.candidates) {
                // Filtrar los que ya probamos
                const probados = candidates.map(c => c.iid);
                const nuevosCandidatos = rescueResult.candidates.filter(c => !probados.includes(c.iid));
                
                if (nuevosCandidatos.length > 0) {
                    for (let j = 0; j < nuevosCandidatos.length; j++) {
                        const cand = nuevosCandidatos[j];
                        const iid = cand.iid;
                        console.log(`[OCR] [RESCUE] Probando candidato IID (${j+1}/${nuevosCandidatos.length}): ${iid} (Score: ${cand.score || '?'})`);
                        
                        if (typeof onProgress === 'function') {
                            onProgress('calling_cid_rescue', {
                                iid: iid,
                                candidateIndex: j + 1,
                                totalCandidates: nuevosCandidatos.length
                            });
                        }

                        const rescueCidStartTime = Date.now();
                        try {
                            const cidResult = await getCID(iid);
                            const cidVal = typeof cidResult === 'string' ? cidResult : cidResult.cid;
                            const backendMethod = typeof cidResult === 'object' ? cidResult.method : 'unknown';
                            const cidElapsed = (Date.now() - rescueCidStartTime) / 1000;
                            
                            return { 
                                success: true, 
                                iid: iid, 
                                cid: cidVal, 
                                strategy: cand.strategy || cand.method || 'v3.3-turbo', 
                                method: cand.method || 'v3.3-turbo', 
                                backendMethod,
                                ocrElapsed: ocrElapsed,
                                cidElapsed: cidElapsed
                            };
                        } catch (e) {
                            lastErrorObj = e;
                            lastPartialIID = iid;
                        }
                    }
                } else {
                    console.log(`[OCR] [RESCUE] No se encontraron nuevos candidatos útiles.`);
                }
            }
        }
        
        lastErrorObj.iid = lastPartialIID;
        throw lastErrorObj;
    }
    
    return { 
        success: false, 
        detectedDigits: lastPartialIID,
        lastError: lastErrorObj ? (lastErrorObj.code || lastErrorObj.message) : 'Error desconocido',
        ocrElapsed: ocrElapsed
    };
}

// Solo extraer IID de imagen (sin pedir CID)
async function ocrExtractOnly(filePath) {
    const ocrResult = await extractFromBackend(filePath);
    if (ocrResult && ocrResult.success) {
        return { 
            success: true, 
            iid: ocrResult.iid, 
            method: ocrResult.method || 'v3.3-turbo', 
            strategy: ocrResult.strategy || ocrResult.method || 'v3.3-turbo' 
        };
    }
    return { success: false };
}

// Mantenemos STRATEGIES como un array vacío o dummy por si bot.js lo exporta/usa
const STRATEGIES = [{ name: 'Backend_Python' }];

module.exports = { initWorker, extractIID: () => null, ocrAndGetCID, ocrExtractOnly, STRATEGIES };
