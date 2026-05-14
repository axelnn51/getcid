// ============================================================
// CID Helper — Obtener Confirmation ID
// ARQUITECTURA: Petición interna a microservicio getcid_python
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

const GETCID_SERVICE_URL = process.env.GETCID_SERVICE_URL || 'http://getcid_python:8000';

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

  console.log(`[CID] Solicitando IID al microservicio Python: ${GETCID_SERVICE_URL}`);
  
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 45000); // 45s porque Playwright puede tardar si hace login

  try {
    const response = await fetch(`${GETCID_SERVICE_URL}/api/getcid`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ iid: cleanIid }),
      signal: controller.signal
    });

    clearTimeout(timeout);
    const data = await response.json();

    if (response.ok && data.success && data.cid) {
      // Devolver como string de bloques separados por guiones para Telegram
      return data.cid;
    }

    // Clasificación de errores basada en el mensaje de error de Python
    const errorText = (data.error || '').toLowerCase();
    
    if (errorText.includes('checksum')) {
      throw new CIDError('INVALID_CHECKSUM', '❌ *IID con checksum inválido*\nUn dígito está incorrecto. Verifica cada bloque contra tu pantalla.', { iid: cleanIid });
    }
    if (errorText.includes('bloquead') || errorText.includes('blocked')) {
      throw new CIDError('KEY_BLOCKED', '🔒 *Clave bloqueada por Microsoft*\nContacta soporte para un reemplazo.', { iid: cleanIid });
    }
    if (errorText.includes('activations') || errorText.includes('límite')) {
      throw new CIDError('TOO_MANY_ACTIVATIONS', '⚠️ *Límite de activaciones alcanzado*\nContacta soporte.', { iid: cleanIid });
    }
    
    throw new CIDError('MS_ERROR', `❌ *Error al procesar IID*\n${data.error || 'Respuesta inválida'}`, { iid: cleanIid });

  } catch (err) {
    clearTimeout(timeout);
    if (err instanceof CIDError) throw err;
    if (err.name === 'AbortError') {
      throw new CIDError('TIMEOUT', '⏱ *Tiempo agotado*\nEl servicio Python tardó más de 45 segundos.', { iid: cleanIid });
    }
    throw new CIDError('NETWORK_ERROR', `❌ *Error de conexión interna:* No se pudo alcanzar getcid_python.`, { iid: cleanIid });
  }
}

module.exports = { getConfirmationID, CIDError };
