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

async function extractFromBackend(filePath) {
    try {
        const form = new FormData();
        form.append('file', fs.createReadStream(filePath));

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
async function ocrAndGetCID(filePath, getCID) {
    const ocrResult = await extractFromBackend(filePath);
    
    if (!ocrResult || !ocrResult.success) {
        return { 
            success: false, 
            detectedDigits: ocrResult ? ocrResult.detected_digits : null,
            lastError: ocrResult ? ocrResult.error : 'Error desconocido de OCR'
        };
    }

    const iid = ocrResult.iid;
    const strategy = ocrResult.strategy;
    const method = ocrResult.method;

    try {
        const cidResult = await getCID(iid);
        const cidVal = typeof cidResult === 'string' ? cidResult : cidResult.cid;
        const backendMethod = typeof cidResult === 'object' ? cidResult.method : 'unknown';
        
        return { 
            success: true, 
            iid: iid, 
            cid: cidVal, 
            strategy: strategy, 
            method: method, 
            backendMethod 
        };
    } catch (e) {
        // Error de Microsoft o de CID
        if (e.code && e.code.startsWith('MS_') || e.code === 'KEY_BLOCKED' || 
            e.code === 'TOO_MANY_ACTIVATIONS' || e.code === 'ACTIVATION_FAILED' ||
            e.code === 'KEY_EXPIRED' || e.code === 'KEY_NOT_GENUINE' ||
            e.code === 'INVALID_PRODUCT' || e.code === 'TIMEOUT' ||
            e.code === 'NO_CID_IN_RESPONSE' || e.code === 'NETWORK_ERROR' ||
            e.code === 'INVALID_CHECKSUM' || e.message === 'INVALID_CHECKSUM') {
            
            e.iid = iid;
            throw e;
        }
        
        return { 
            success: false, 
            detectedDigits: iid,
            lastError: e.code || e.message
        };
    }
}

// Solo extraer IID de imagen (sin pedir CID)
async function ocrExtractOnly(filePath) {
    const ocrResult = await extractFromBackend(filePath);
    if (ocrResult && ocrResult.success) {
        return { 
            success: true, 
            iid: ocrResult.iid, 
            method: ocrResult.method, 
            strategy: ocrResult.strategy 
        };
    }
    return { success: false };
}

// Mantenemos STRATEGIES como un array vacío o dummy por si bot.js lo exporta/usa
const STRATEGIES = [{ name: 'Backend_Python' }];

module.exports = { initWorker, extractIID: () => null, ocrAndGetCID, ocrExtractOnly, STRATEGIES };
