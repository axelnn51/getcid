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

const GETCID_SERVICE_URL = process.env.GETCID_SERVICE_URL || 'http://getcid_backend:8000';

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

  console.log(`[CID] Solicitando CID al backend: ${GETCID_SERVICE_URL}`);
  
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 60000);

  try {
    const response = await fetch(`${GETCID_SERVICE_URL}/check_pid`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pid: cleanIid }),
      signal: controller.signal
    });

    clearTimeout(timeout);
    const data = await response.json();

    if (response.ok && data.success && data.cid) {
      return data.cid;
    }

    // Clasificación de errores basada en el mensaje de error del backend
    const errorText = (data.error || data.message || '').toLowerCase();
    
    if (errorText.includes('checksum') || errorText.includes('inválido')) {
      throw new CIDError('INVALID_CHECKSUM', '❌ *IID con checksum inválido*\nUn dígito está incorrecto. Verifica cada bloque contra tu pantalla.', { iid: cleanIid });
    }
    if (errorText.includes('bloquead') || errorText.includes('blocked')) {
      throw new CIDError('KEY_BLOCKED', '🔒 *Clave bloqueada por Microsoft*\nContacta soporte para un reemplazo.', { iid: cleanIid });
    }
    if (errorText.includes('límite') || errorText.includes('limit') || errorText.includes('excedió')) {
      throw new CIDError('TOO_MANY_ACTIVATIONS', '⚠️ *Límite de activaciones alcanzado*\nContacta soporte.', { iid: cleanIid });
    }
    
    throw new CIDError('MS_ERROR', `❌ *Error al procesar IID*\n${data.error || data.message || 'Respuesta inválida'}`, { iid: cleanIid });

  } catch (err) {
    clearTimeout(timeout);
    console.error('[CID_HELPER ERROR] Falló la petición al backend:', err);
    if (err instanceof CIDError) throw err;
    if (err.name === 'AbortError') {
      throw new CIDError('TIMEOUT', '⏱ *Tiempo agotado*\nEl servicio tardó más de 60 segundos. Intenta de nuevo.', { iid: cleanIid });
    }
    throw new CIDError('NETWORK_ERROR', `❌ *Error de conexión interna:* No se pudo alcanzar el backend. Detalle: ${err.message}`, { iid: cleanIid });
  }
}

module.exports = { getConfirmationID, CIDError };
