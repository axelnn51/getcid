// ============================================================
// CID Helper — Obtener Confirmation ID
// ARQUITECTURA: Petición interna a microservicio getcid_backend
// ============================================================

class CIDError extends Error {
  constructor(code, userMessage, details = {}) {
    super(code);
    this.name = 'CIDError';
    this.code = code;
    this.userMessage = userMessage;
    this.iid = details.iid || null;
    this.msResponse = details.msResponse || null;
  }
}

const path = require('path');
const { execFile } = require('child_process');

const GETCID_SERVICE_URL = process.env.GETCID_SERVICE_URL || 'http://getcid_backend:8000';

const pythonBin = process.env.PYTHON_BIN || (process.platform === 'win32' ? 'python' : 'python3');

function runPythonCID(cleanIid) {
  return new Promise((resolve, reject) => {
    const scriptPath = path.resolve(__dirname, '..', 'cli_cid.py');
    execFile(pythonBin, [scriptPath, cleanIid], { timeout: 60000 }, (error, stdout, stderr) => {
      if (error) return reject(error);
      try {
        const lines = stdout.trim().split('\n');
        const lastLine = lines[lines.length - 1];
        const data = JSON.parse(lastLine);
        resolve(data);
      } catch (e) {
        reject(new Error(`Error parseando respuesta de Python CLI: ${stdout}`));
      }
    });
  });
}

async function getConfirmationID(iid) {
  if (!iid || typeof iid !== 'string') {
    throw new CIDError('INVALID_IID', '❌ *IID vacío o inválido*', { iid });
  }
  const cleanIid = iid.replace(/\D/g, '');
  if (cleanIid.length < 54) {
    throw new CIDError('IID_TOO_SHORT',
      `❌ *IID demasiado corto*\n${cleanIid.length} dígitos detectados, se necesitan 54-63.`,
      { iid: cleanIid });
  }
  if (cleanIid.length > 63) {
    throw new CIDError('IID_TOO_LONG',
      `❌ *IID demasiado largo*\n${cleanIid.length} dígitos, máximo 63.`,
      { iid: cleanIid });
  }

  let data = null;
  const urlsToTry = [
    GETCID_SERVICE_URL,
    'http://127.0.0.1:8000',
    'http://localhost:8000'
  ];

  // 1. Intentar vía HTTP endpoints
  for (const baseUrl of urlsToTry) {
    if (!baseUrl) continue;
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 15000);
      const response = await fetch(`${baseUrl}/check_pid`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pid: cleanIid }),
        signal: controller.signal
      });
      clearTimeout(timeout);
      if (response.ok) {
        data = await response.json();
        break;
      }
    } catch (e) {
      // Ignorar fallo y probar siguiente endpoint
    }
  }

  // 2. Si fallaron los endpoints HTTP, invocar Python CLI directamente
  if (!data) {
    console.log('[CID] Backend HTTP no disponible, ejecutando CLI local de Python...');
    try {
      data = await runPythonCID(cleanIid);
    } catch (err) {
      console.error('[CID_HELPER CLI ERROR]:', err.message);
      throw new CIDError('NETWORK_ERROR', `❌ *Error conectando al motor CID:* ${err.message}`, { iid: cleanIid });
    }
  }

  if (data && data.success && data.cid) {
    return { cid: data.cid, method: data.method || 'unknown' };
  }

  // Clasificación de errores basada en el mensaje de error del backend
  const errorText = (data?.error || data?.message || data?.error_message || '').toLowerCase();
  
  if (data?.code === '0x90' || errorText.includes('checksum') || errorText.includes('inválido')) {
    throw new CIDError('INVALID_CHECKSUM', '❌ *IID con checksum inválido*\nUn dígito está incorrecto. Verifica cada bloque contra tu pantalla.', { iid: cleanIid });
  }
  if (data?.code === '0x67' || errorText.includes('bloquead') || errorText.includes('blocked')) {
    throw new CIDError('KEY_BLOCKED', '🔒 *Clave bloqueada por Microsoft*\nContacta soporte para un reemplazo.', { iid: cleanIid });
  }
  if (data?.code === '0xD6' || data?.code === '0x71' || data?.code === '0x7F' || errorText.includes('límite') || errorText.includes('limit') || errorText.includes('excedió')) {
    throw new CIDError('TOO_MANY_ACTIVATIONS', '⚠️ *Límite de activaciones alcanzado*\nEsta licencia alcanzó el límite por lote de Microsoft y requiere activación telefónica individual (1 PC).', { iid: cleanIid });
  }
  
  throw new CIDError('MS_ERROR', `❌ *Error al procesar IID*\n${data?.error || data?.message || data?.error_message || 'Respuesta inválida'}`, { iid: cleanIid });
}

module.exports = { getConfirmationID, CIDError };
