const crypto = require('crypto');

// ============================================================
// CID Helper — Obtener Confirmation ID de Microsoft
// Con detección completa de errores de activación
// ============================================================

// Base64URL Encoding
function eI(t) {
  let e = typeof t === 'string' ? new TextEncoder().encode(t) : t;
  return Buffer.from(e).toString('base64').replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// Generar una clave ECDSA P-256 en webcrypto
let tI = null;
async function yT() {
  if (!tI) {
    tI = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  }
  return tI;
}

// Generar DPoP Token
async function generateDPoPToken(htu, htm) {
  const { privateKey, publicKey } = await yT();
  const jwk = await crypto.subtle.exportKey("jwk", publicKey);
  const header = { alg: "ES256", typ: "dpop+jwt", jwk };
  const payload = { htu, htm, jti: crypto.randomUUID(), iat: Math.floor(Date.now() / 1000) };
  const s = eI(JSON.stringify(header));
  const l = eI(JSON.stringify(payload));
  const u = `${s}.${l}`;
  const signature = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, privateKey, new TextEncoder().encode(u));
  return `${u}.${eI(signature)}`;
}

// ============================================================
// Error personalizado con código y contexto
// ============================================================
class CIDError extends Error {
  constructor(code, userMessage, details = {}) {
    super(code);
    this.name = 'CIDError';
    this.code = code;
    this.userMessage = userMessage;
    this.iid = details.iid || null;
    this.httpStatus = details.httpStatus || null;
    this.msResponse = details.msResponse || null;
  }
}

// ============================================================
// Clasificar errores de Microsoft
// REGLA CLAVE: Solo clasificar si NO hay un CID válido.
// Solo buscar en campos de error específicos, NUNCA en todo el JSON.
// ============================================================
function classifyMicrosoftError(data, httpStatus, iid) {
  // 1. Checksum inválido — siempre verificar primero
  if (data.validChecksum === false) {
    return new CIDError(
      'INVALID_CHECKSUM',
      '❌ *IID con checksum inválido*\nUn dígito está incorrecto. Verifica cada bloque de 7 dígitos contra tu pantalla.',
      { iid, httpStatus, msResponse: data }
    );
  }

  // 2. Si hay un CID válido en la respuesta → NO ES UN ERROR, retornar null
  const cidValue = data.cid || data.CID;
  if (cidValue && typeof cidValue === 'string' && cidValue.length >= 48) {
    return null; // ¡ÉXITO! No clasificar como error
  }

  // 3. Si activationSuccessful es true → NO ES UN ERROR
  if (data.activationSuccessful === true) {
    return null;
  }

  // ============================================================
  // Solo llegar aquí si NO hay CID y NO fue exitoso
  // Ahora sí clasificar el error usando SOLO campos de error
  // ============================================================

  // Extraer campos de error específicos de Microsoft
  const errorCode = (data.errorCode || data.ErrorCode || data.error_code || data.statusCode || '').toString().toLowerCase();
  const errorMessage = (data.errorMessage || data.ErrorMessage || data.error_message || 
                        data.message || data.Message || data.statusMessage || 
                        data.description || data.Description || data.reason || '').toString().toLowerCase();
  const errorDetail = (data.detail || data.Detail || data.details || '').toString().toLowerCase();
  
  // Combinar solo campos de error (NO todo el JSON)
  const errorText = `${errorCode} ${errorMessage} ${errorDetail}`;

  // Key bloqueada
  if (errorText.includes('blocked') || errorText.includes('block') || 
      errorCode === 'blocked' || errorCode === 'key_blocked') {
    return new CIDError(
      'KEY_BLOCKED',
      '🔒 *Clave bloqueada por Microsoft*\nEsta licencia ha sido bloqueada. Contacta a soporte para un reemplazo.',
      { iid, httpStatus, msResponse: data }
    );
  }

  // Demasiadas activaciones
  if (errorText.includes('too many activation') || errorText.includes('activation limit') ||
      errorText.includes('max activation') || errorText.includes('limit reached') ||
      errorCode === 'too_many_activations' || errorCode === 'activation_limit') {
    return new CIDError(
      'TOO_MANY_ACTIVATIONS',
      '⚠️ *Límite de activaciones alcanzado*\nEsta licencia ya se activó en demasiados dispositivos. Contacta soporte.',
      { iid, httpStatus, msResponse: data }
    );
  }

  // Producto no soportado / inválido
  if (errorText.includes('invalid product') || errorText.includes('not supported') ||
      errorText.includes('unsupported product') || errorText.includes('unknown product') ||
      errorCode === 'invalid_product' || errorCode === 'unsupported') {
    return new CIDError(
      'INVALID_PRODUCT',
      '❌ *Producto no soportado*\nEste IID corresponde a un producto que no se puede activar por teléfono.',
      { iid, httpStatus, msResponse: data }
    );
  }

  // Key expirada — SOLO en campos de error, NO en todo el JSON
  if (errorText.includes('key expired') || errorText.includes('license expired') || 
      errorText.includes('product expired') || errorText.includes('subscription expired') ||
      errorCode === 'expired' || errorCode === 'key_expired' || errorCode === 'license_expired') {
    return new CIDError(
      'KEY_EXPIRED',
      '⏰ *Licencia expirada*\nEsta licencia ha expirado y ya no se puede activar.',
      { iid, httpStatus, msResponse: data }
    );
  }

  // Key pirata / no genuina
  if (errorText.includes('not genuine') || errorText.includes('counterfeit') ||
      errorText.includes('pirated') || errorText.includes('blacklisted') ||
      errorCode === 'not_genuine' || errorCode === 'blacklisted') {
    return new CIDError(
      'KEY_NOT_GENUINE',
      '🚫 *Licencia no válida*\nMicrosoft no reconoce esta licencia como genuina. Contacta soporte.',
      { iid, httpStatus, msResponse: data }
    );
  }

  // Grace period — SOLO si es explícito en error, no en campos genéricos
  if (errorText.includes('grace period') || errorText.includes('trial period') ||
      errorCode === 'grace_period' || errorCode === 'trial') {
    return new CIDError(
      'GRACE_PERIOD',
      '⏳ *Período de gracia*\nEl producto está en período de prueba. Instala una licencia válida primero.',
      { iid, httpStatus, msResponse: data }
    );
  }

  // Error genérico con errorCode del API
  if (data.errorCode || data.ErrorCode) {
    const code = data.errorCode || data.ErrorCode;
    const msg = data.errorMessage || data.ErrorMessage || data.message || '';
    return new CIDError(
      `MS_${code}`,
      `❌ *Error de Microsoft (${code})*\n${msg || 'Error desconocido durante la activación.'}`,
      { iid, httpStatus, msResponse: data }
    );
  }

  // activationSuccessful explícitamente false (sin CID)
  if (data.activationSuccessful === false) {
    // Incluir el mensaje de error si hay uno
    const reason = data.errorMessage || data.message || data.reason || 'Motivo no especificado';
    return new CIDError(
      'ACTIVATION_FAILED',
      `❌ *Activación rechazada por Microsoft*\n${reason}`,
      { iid, httpStatus, msResponse: data }
    );
  }

  return null; // No error detectado
}

// ============================================================
// Función principal para obtener el CID desde el IID
// ============================================================
async function getConfirmationID(iid) {
  // Validación previa
  if (!iid || typeof iid !== 'string') {
    throw new CIDError('INVALID_IID', '❌ *IID vacío o inválido*\nNo se proporcionó un IID válido.', { iid });
  }

  const cleanIid = iid.replace(/\D/g, '');

  if (cleanIid.length < 54) {
    throw new CIDError(
      'IID_TOO_SHORT',
      `❌ *IID demasiado corto*\nSe detectaron ${cleanIid.length} dígitos, se necesitan al menos 54 (9 bloques de 6+).`,
      { iid: cleanIid }
    );
  }

  if (cleanIid.length > 63) {
    throw new CIDError(
      'IID_TOO_LONG',
      `❌ *IID demasiado largo*\nSe detectaron ${cleanIid.length} dígitos, el máximo es 63.`,
      { iid: cleanIid }
    );
  }

  const endpoint = "https://visualsupport.microsoft.com/api/productActivation/validateIID";
  const dpopToken = await generateDPoPToken("/api/productActivation/validateIID", "POST");
  const sid = `app_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
  const digits = Math.floor(cleanIid.length / 9);

  // Timeout de 15 segundos
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);

  try {
    const response = await fetch(endpoint, {
      method: "POST",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer govUrlID",
        "DPoP": dpopToken,
        "x-session-id": sid
      },
      body: JSON.stringify({
        IID: cleanIid,
        ProductType: "windows",
        productGroup: "Windows",
        productName: "Windows 11",
        numberOfDigits: digits,
        Country: "CHN",
        Region: "APAC",
        InstalledDevices: 1,
        OverrideStatusCode: "MUL",
        InitialReasonCode: "45164"
      })
    });

    clearTimeout(timeout);

    const text = await response.text();
    let data;
    try { data = JSON.parse(text); } catch { data = { raw: text }; }

    // Log para diagnóstico (solo en desarrollo)
    if (process.env.NODE_ENV !== 'production') {
      console.log('[CID] Microsoft response:', JSON.stringify(data, null, 2));
    }

    // ============================================================
    // PASO 1: ¿Hay un CID válido? → ÉXITO INMEDIATO
    // SIEMPRE verificar esto PRIMERO, antes de cualquier clasificación de error
    // ============================================================
    const cidValue = data.cid || data.CID;
    if (cidValue && typeof cidValue === 'string' && cidValue.length >= 48) {
      return cidValue.match(/\d{6}/g) || cidValue;
    }

    // Si activationSuccessful pero CID en formato no estándar
    if (data.activationSuccessful === true || data.activationSuccessful === 'true') {
      // Intentar extraer CID de otros campos posibles
      const altCid = data.confirmationId || data.ConfirmationId || data.confirmation_id;
      if (altCid && typeof altCid === 'string' && altCid.length >= 48) {
        return altCid.match(/\d{6}/g) || altCid;
      }
      // Éxito pero sin CID en formato conocido, devolver la data completa
      return data;
    }

    // ============================================================
    // PASO 2: No hay CID → Verificar checksum inválido (para OCR retry)
    // ============================================================
    if (data.validChecksum === false) {
      throw new CIDError(
        'INVALID_CHECKSUM',
        '❌ *IID con checksum inválido*\nUn dígito está incorrecto. Verifica cada bloque de 7 dígitos contra tu pantalla.',
        { iid: cleanIid, httpStatus: response.status, msResponse: data }
      );
    }

    // ============================================================
    // PASO 3: No hay CID, checksum OK → Clasificar error de Microsoft
    // ============================================================
    const msError = classifyMicrosoftError(data, response.status, cleanIid);
    if (msError) throw msError;

    // ============================================================
    // PASO 4: HTTP error sin clasificar
    // ============================================================
    if (!response.ok) {
      const statusMessages = {
        400: 'Solicitud inválida. Verifica el formato del IID.',
        401: 'Error de autenticación con Microsoft. Intenta más tarde.',
        403: 'Microsoft rechazó la solicitud. El IID podría ser para un producto no soportado o bloqueado.',
        404: 'Endpoint de Microsoft no encontrado. Intenta más tarde.',
        429: 'Demasiadas solicitudes a Microsoft. Espera 1-2 minutos e intenta de nuevo.',
        500: 'Error interno de Microsoft. Intenta más tarde.',
        502: 'Servidor de Microsoft no disponible. Intenta más tarde.',
        503: 'Servicio de Microsoft temporalmente no disponible. Intenta en unos minutos.',
      };
      const msg = statusMessages[response.status] || `Error HTTP ${response.status} de Microsoft.`;
      throw new CIDError(
        `MS_HTTP_${response.status}`,
        `❌ *Error ${response.status}*\n${msg}`,
        { iid: cleanIid, httpStatus: response.status, msResponse: data }
      );
    }

    // ============================================================
    // PASO 5: Response OK pero sin CID — Error inesperado
    // ============================================================
    throw new CIDError(
      'NO_CID_IN_RESPONSE',
      '❌ *Sin CID en la respuesta*\nMicrosoft respondió OK pero no incluyó un Confirmation ID.',
      { iid: cleanIid, msResponse: data }
    );

  } catch (err) {
    clearTimeout(timeout);
    if (err instanceof CIDError) throw err;
    if (err.name === 'AbortError') {
      throw new CIDError(
        'TIMEOUT',
        '⏱ *Tiempo agotado*\nMicrosoft no respondió en 15 segundos. Intenta de nuevo.',
        { iid: cleanIid }
      );
    }
    // Error de red
    throw new CIDError(
      'NETWORK_ERROR',
      `❌ *Error de conexión*\nNo se pudo conectar con Microsoft: ${err.message}`,
      { iid: cleanIid }
    );
  }
}

module.exports = { getConfirmationID, CIDError };
