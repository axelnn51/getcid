const crypto = require('crypto');
const { getCIDViaPuppeteer } = require('./cid_puppeteer');

// ============================================================
// CID Helper — Obtener Confirmation ID
// ARQUITECTURA: Bot/Web → Cloudflare Worker → Microsoft
// El Worker proxy es necesario porque Microsoft bloquea
// peticiones directas desde IPs de VPS/residenciales.
// ============================================================

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
// URL del Worker Proxy en Cloudflare
// Configura WORKER_PROXY_URL en .env con la URL de tu worker
// Ejemplo: https://getcid-proxy.tu-usuario.workers.dev
// ============================================================
const WORKER_PROXY_URL = process.env.WORKER_PROXY_URL || '';
const WORKER_API_KEY = process.env.WORKER_API_KEY || '';

// ============================================================
// Base64URL Encoding (para modo directo/fallback)
// ============================================================
function eI(t) {
  let e = typeof t === 'string' ? new TextEncoder().encode(t) : t;
  return Buffer.from(e).toString('base64').replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

let tI = null;
async function yT() {
  if (!tI) {
    tI = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  }
  return tI;
}

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
// Clasificar errores de Microsoft
// Solo buscar en campos de error específicos, NUNCA en todo el JSON
// Solo clasificar si NO hay CID válido
// ============================================================
function classifyError(data, httpStatus, iid) {
  // Si hay CID válido → NO es error
  const cidValue = data?.cid || data?.CID;
  if (cidValue && typeof cidValue === 'string' && cidValue.length >= 48) return null;
  if (data?.activationSuccessful === true) return null;

  // Checksum inválido
  if (data?.validChecksum === false) {
    return new CIDError('INVALID_CHECKSUM',
      '❌ *IID con checksum inválido*\nUn dígito está incorrecto. Verifica cada bloque contra tu pantalla.',
      { iid, httpStatus, msResponse: data });
  }

  // Campos de error específicos
  const errorCode = (data?.errorCode || data?.ErrorCode || data?.error || '').toString().toLowerCase();
  const errorMsg = (data?.errorMessage || data?.ErrorMessage || data?.message || data?.Message || '').toString().toLowerCase();
  const errorText = `${errorCode} ${errorMsg}`;

  if (errorText.includes('blocked') || errorCode === 'blocked') {
    return new CIDError('KEY_BLOCKED', '🔒 *Clave bloqueada por Microsoft*\nContacta soporte para un reemplazo.', { iid, httpStatus, msResponse: data });
  }
  if (errorText.includes('too many activation') || errorText.includes('activation limit')) {
    return new CIDError('TOO_MANY_ACTIVATIONS', '⚠️ *Límite de activaciones alcanzado*\nContacta soporte.', { iid, httpStatus, msResponse: data });
  }
  if (errorText.includes('invalid product') || errorText.includes('not supported')) {
    return new CIDError('INVALID_PRODUCT', '❌ *Producto no soportado para activación telefónica.*', { iid, httpStatus, msResponse: data });
  }
  if (errorText.includes('key expired') || errorText.includes('license expired') || errorCode === 'expired') {
    return new CIDError('KEY_EXPIRED', '⏰ *Licencia expirada.*\nContacta soporte.', { iid, httpStatus, msResponse: data });
  }
  if (errorText.includes('not genuine') || errorText.includes('blacklist')) {
    return new CIDError('KEY_NOT_GENUINE', '🚫 *Licencia no válida.*\nContacta soporte.', { iid, httpStatus, msResponse: data });
  }
  if (data?.errorCode || data?.ErrorCode) {
    const code = data.errorCode || data.ErrorCode;
    return new CIDError(`MS_${code}`, `❌ *Error Microsoft (${code})*\n${data.errorMessage || data.message || ''}`, { iid, httpStatus, msResponse: data });
  }
  if (data?.activationSuccessful === false) {
    return new CIDError('ACTIVATION_FAILED', `❌ *Activación rechazada*\n${data.message || data.errorMessage || ''}`, { iid, httpStatus, msResponse: data });
  }

  return null;
}

// ============================================================
// MODO 1: Via Worker Proxy (Cloudflare) — PREFERIDO
// ============================================================
async function getCIDViaProxy(cleanIid) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);

  try {
    const headers = { 'Content-Type': 'application/json' };
    if (WORKER_API_KEY) headers['X-API-Key'] = WORKER_API_KEY;

    const response = await fetch(WORKER_PROXY_URL, {
      method: 'POST',
      signal: controller.signal,
      headers,
      body: JSON.stringify({ iid: cleanIid })
    });

    clearTimeout(timeout);
    const data = await response.json();

    // Worker proxy ya extrae el CID
    if (data.success && data.cid) {
      return data.cid; // Array de bloques de 6 dígitos o string
    }

    if (data.cidRaw && typeof data.cidRaw === 'string' && data.cidRaw.length >= 48) {
      return data.cidRaw.match(/\d{6}/g) || data.cidRaw;
    }

    // Worker devolvió error
    if (data.error === 'INVALID_CHECKSUM') {
      throw new CIDError('INVALID_CHECKSUM',
        '❌ *IID con checksum inválido*\nUn dígito está incorrecto.',
        { iid: cleanIid, httpStatus: data.httpStatus, msResponse: data.msResponse });
    }

    // Clasificar el error basado en msResponse del worker
    if (data.msResponse) {
      const msErr = classifyError(data.msResponse, data.httpStatus, cleanIid);
      if (msErr) throw msErr;
    }

    // Error genérico del worker
    throw new CIDError(
      `PROXY_ERROR`,
      `❌ *Error del servidor de activación*\n${data.message || data.error || 'Sin respuesta válida.'}`,
      { iid: cleanIid, httpStatus: data.httpStatus, msResponse: data }
    );
  } catch (err) {
    clearTimeout(timeout);
    if (err instanceof CIDError) throw err;
    if (err.name === 'AbortError') {
      throw new CIDError('TIMEOUT', '⏱ *Tiempo agotado*\nEl proxy no respondió en 20s.', { iid: cleanIid });
    }
    throw new CIDError('PROXY_NETWORK_ERROR',
      `❌ *Error conectando con proxy*\n${err.message}`,
      { iid: cleanIid });
  }
}

// ============================================================
// MODO 2: Directo a Microsoft — FALLBACK
// (solo funciona desde IPs que Microsoft acepte)
// ============================================================
async function getCIDDirect(cleanIid) {
  const endpoint = "https://visualsupport.microsoft.com/api/productActivation/validateIID";
  const dpopToken = await generateDPoPToken("/api/productActivation/validateIID", "POST");
  const sid = `app_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
  const digits = Math.floor(cleanIid.length / 9);

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
        IID: cleanIid, ProductType: "windows", productGroup: "Windows", productName: "Windows 11",
        numberOfDigits: digits, Country: "CHN", Region: "APAC", InstalledDevices: 1,
        OverrideStatusCode: "MUL", InitialReasonCode: "45164"
      })
    });

    clearTimeout(timeout);
    const text = await response.text();
    let data;
    try { data = JSON.parse(text); } catch { data = { raw: text }; }

    // PASO 1: ¿Hay CID? → ÉXITO
    const cidValue = data.cid || data.CID;
    if (cidValue && typeof cidValue === 'string' && cidValue.length >= 48) {
      return cidValue.match(/\d{6}/g) || cidValue;
    }
    if (data.activationSuccessful === true) {
      const altCid = data.confirmationId || data.ConfirmationId;
      if (altCid && typeof altCid === 'string' && altCid.length >= 48) {
        return altCid.match(/\d{6}/g) || altCid;
      }
      return data;
    }

    // PASO 2: Clasificar error
    const msError = classifyError(data, response.status, cleanIid);
    if (msError) throw msError;

    // PASO 3: HTTP error
    if (!response.ok) {
      const statusMsgs = {
        400: 'Solicitud inválida.',
        401: 'Error de autenticación.',
        403: 'Microsoft rechazó la solicitud (403). Necesitas activar el proxy Cloudflare Worker.',
        429: 'Demasiadas solicitudes. Espera 1-2 min.',
        500: 'Error interno de Microsoft.',
        502: 'Servidor no disponible.',
        503: 'Servicio temporalmente no disponible.',
      };
      throw new CIDError(`MS_HTTP_${response.status}`,
        `❌ *Error ${response.status}*\n${statusMsgs[response.status] || 'Error HTTP de Microsoft.'}`,
        { iid: cleanIid, httpStatus: response.status, msResponse: data });
    }

    throw new CIDError('NO_CID_IN_RESPONSE',
      '❌ *Sin CID en la respuesta*',
      { iid: cleanIid, msResponse: data });

  } catch (err) {
    clearTimeout(timeout);
    if (err instanceof CIDError) throw err;
    if (err.name === 'AbortError') {
      throw new CIDError('TIMEOUT', '⏱ *Tiempo agotado (15s)*', { iid: cleanIid });
    }
    throw new CIDError('NETWORK_ERROR', `❌ *Error de red:* ${err.message}`, { iid: cleanIid });
  }
}

// ============================================================
// Función principal — Intenta proxy primero, directo como fallback
// ============================================================
async function getConfirmationID(iid) {
  // Validación
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

  // Modo 1: Puppeteer Headless (Gratis, sin login, predeterminado)
  console.log('[CID] Usando motor Puppeteer en Ubuntu Server');
  try {
    return await getCIDViaPuppeteer(cleanIid);
  } catch (err) {
    // Si Puppeteer falla por un error interno de red/automatización, intentar Worker Proxy como fallback
    if (err.code === 'NETWORK_ERROR' || err.code === 'TIMEOUT') {
      console.log('[CID] Puppeteer falló por red/automatización, intentando Worker proxy como fallback...');
      if (WORKER_PROXY_URL) {
        try {
          return await getCIDViaProxy(cleanIid);
        } catch (proxyErr) {
          console.log('[CID] Proxy falló, intentando directo como último recurso...');
          return await getCIDDirect(cleanIid);
        }
      }
      return await getCIDDirect(cleanIid);
    }
    throw err; // Errores de licencia (bloqueada, límite, expirada) se propagan directamente
  }
}

module.exports = { getConfirmationID, CIDError };
