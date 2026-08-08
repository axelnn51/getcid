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
  // 90s: el sistema Python puede tardar hasta ~45s internamente (refresh + espera de Playwright)
  // antes de devolver el CID o el error final
  const timeout = setTimeout(() => controller.abort(), 90000);

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
      // Devolver como string de bloques separados por guiones para Telegram
      return data.cid;
    }
    
    // Si la API devuelve 401 por falta de token (Extractor no ha corrido)
    if (response.status === 401) {
       throw new CIDError('UNAUTHORIZED', '❌ *Sistema Desconectado*\nEl servidor está esperando la extracción de sesión local. Contacta al administrador.', { iid: cleanIid });
    }

    // Clasificación de errores basada en el mensaje de error de Python
    const errorText = (data.error || '').toLowerCase();
    const errorCode = (data.code || '').toUpperCase();
    
    // Token en renovación automática (Python lo detectó y ya lanzó Playwright)
    if (errorCode === 'MS_TOKEN_RENEWING' || errorText.includes('ciclo infinito') || errorText.includes('token expiró')) {
      throw new CIDError('MS_TOKEN_RENEWING',
        '🔄 *Sistema en renovación de token*\nEl servicio está obteniendo credenciales nuevas automáticamente. Por favor, reintenta en 2–3 minutos.',
        { iid: cleanIid });
    }
    
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
    console.error('[CID_HELPER ERROR] Falló la petición a getcid_python:', err);
    if (err instanceof CIDError) throw err;
    if (err.name === 'AbortError') {
      throw new CIDError('TIMEOUT', '⏱ *Tiempo agotado*\nEl servicio tardó más de 90 segundos. Intenta de nuevo.', { iid: cleanIid });
    }
    throw new CIDError('NETWORK_ERROR', `❌ *Error de conexión interna:* No se pudo alcanzar getcid_python. Detalle: ${err.message}`, { iid: cleanIid });
  }
}

module.exports = { getConfirmationID, CIDError };
