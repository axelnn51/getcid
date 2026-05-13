const crypto = require('crypto');
const initCycleTLS = require('cycletls');

// ============================================================
// CID Helper — Obtener Confirmation ID
// ARQUITECTURA: Conexión Directa (TLS Spoofing + DPoP) → Cloudflare Worker Proxy
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
// Cliente CycleTLS — suplantar huella TLS (JA3) de Chrome real
// Evita bloqueos WAF de Azure Front Door en servidores Linux
// ============================================================
let cycleTLS;
async function getCycleTLS() {
  if (!cycleTLS) {
    cycleTLS = await initCycleTLS();
  }
  return cycleTLS;
}

// ============================================================
// Base64URL Encoding (para DPoP)
// ============================================================
function eI(t) {
  let e = typeof t === 'string' ? new TextEncoder().encode(t) : t;
  return Buffer.from(e).toString('base64').replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// Par de claves ECDSA reutilizable durante toda la vida del proceso
let tI = null;
async function yT() {
  if (!tI) {
    tI = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  }
  return tI;
}

/**
 * Genera un token DPoP (Demonstrating Proof-of-Possession) — RFC 9449
 *
 * Lógica criptográfica:
 * 1. Par de claves ECDSA P-256 generado una sola vez y reutilizado por proceso.
 * 2. JWK Thumbprint (jkt) calculado según RFC 7638: hash SHA-256 de las
 *    propiedades canónicas de la clave pública (crv, kty, x, y en orden).
 * 3. El header del JWT incluye la clave pública completa (jwk) para que el
 *    servidor pueda verificar la firma sin un directorio externo.
 * 4. El payload vincula el token a la petición exacta: método (htm),
 *    URL (htu), identificador único (jti) y timestamp (iat).
 * 5. Si el servidor devuelve un DPoP-Nonce, se incluye en el payload para
 *    prevenir ataques de replay.
 */
async function generateDPoPToken(htu, htm, nonce = null) {
  const { privateKey, publicKey } = await yT();
  const jwk = await crypto.subtle.exportKey("jwk", publicKey);

  // JWK Thumbprint — vincula de forma única la clave pública a este token
  const canonicalJwk = JSON.stringify({ crv: jwk.crv, kty: jwk.kty, x: jwk.x, y: jwk.y });
  const jktHash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonicalJwk));
  const jkt = eI(new Uint8Array(jktHash));

  const header = { alg: "ES256", typ: "dpop+jwt", jwk };
  const payload = {
    htu,
    htm,
    jti: crypto.randomUUID(),
    iat: Math.floor(Date.now() / 1000),
    jkt
  };
  if (nonce) payload.nonce = nonce;

  const s = eI(JSON.stringify(header));
  const l = eI(JSON.stringify(payload));
  const u = `${s}.${l}`;

  const signature = await crypto.subtle.sign(
    { name: "ECDSA", hash: "SHA-256" },
    privateKey,
    new TextEncoder().encode(u)
  );

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
// MODO 1: Directo a Microsoft con TLS Spoofing + DPoP — PREFERIDO
// Suplanta huella JA3 de Chrome/Windows y resuelve el desafío
// DPoP-Nonce dinámicamente para evadir el WAF de Azure Front Door.
// ============================================================
async function getCIDDirect(cleanIid) {
  const endpoint = "https://visualsupport.microsoft.com/api/productActivation/validateIID";
  const htu = "/api/productActivation/validateIID";
  const htm = "POST";

  const client = await getCycleTLS();
  const sid = `app_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
  const digits = Math.floor(cleanIid.length / 9);

  const payloadData = {
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
  };

  // Función interna que ejecuta la petición con el DPoP token correcto
  async function performRequest(nonce = null) {
    const dpopToken = await generateDPoPToken(htu, htm, nonce);
    return await client(endpoint, {
      method: htm,
      body: JSON.stringify(payloadData),
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer govUrlID",
        "DPoP": dpopToken,
        "x-session-id": sid,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://visualsupport.microsoft.com",
        "Referer": "https://visualsupport.microsoft.com/"
      },
      // JA3 Fingerprint de Chrome 120 en Windows 10/11
      ja3: "771,4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13-18-51-45-43-27-17513-21,29-23-24,0",
      userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }, 15000);
  }

  try {
    // Petición inicial sin Nonce
    console.log('[DPoP] Iniciando desafío de autenticación...');
    let response = await performRequest();

    // Si el servidor devuelve un DPoP-Nonce, reintenta con él para completar el handshake
    const dpopNonce = response.headers?.["dpop-nonce"] || response.headers?.["DPoP-Nonce"];
    if (dpopNonce) {
      console.log('[DPoP] Nonce detectado, reintentando con firma completa...');
      response = await performRequest(dpopNonce);
    }

    // cycleTLS parsea el body automáticamente si es JSON
    const data = typeof response.body === 'string' ? JSON.parse(response.body) : response.body;

    // PASO 1: ¿Hay CID? → ÉXITO
    const cidValue = data?.cid || data?.CID || data?.confirmationId || data?.ConfirmationId;
    if (cidValue && typeof cidValue === 'string' && cidValue.length >= 48) {
      return cidValue.match(/\d{6}/g) || cidValue;
    }
    if (data?.activationSuccessful === true) {
      return data.cid || data.confirmationId || data;
    }

    // PASO 2: Clasificar error de Microsoft
    const msError = classifyError(data, response.status, cleanIid);
    if (msError) throw msError;

    // PASO 3: Errores HTTP
    if (response.status !== 200) {
      throw new CIDError(`MS_HTTP_${response.status}`,
        `❌ *Error ${response.status}*\nAcceso denegado persistente o IID bloqueado.`,
        { iid: cleanIid, httpStatus: response.status, msResponse: data });
    }

    throw new CIDError('NO_CID_IN_RESPONSE',
      '❌ *Sin CID en la respuesta de Microsoft*',
      { iid: cleanIid, msResponse: data });

  } catch (err) {
    if (err instanceof CIDError) throw err;
    throw new CIDError('NETWORK_ERROR', `❌ *Error de red (TLS/DPoP):* ${err.message}`, { iid: cleanIid });
  }
}

// ============================================================
// MODO 2: Via Worker Proxy (Cloudflare) — FALLBACK
// Se activa si la conexión directa falla por red o firma
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

    if (data.success && data.cid) {
      return data.cid;
    }
    if (data.cidRaw && typeof data.cidRaw === 'string' && data.cidRaw.length >= 48) {
      return data.cidRaw.match(/\d{6}/g) || data.cidRaw;
    }
    if (data.error === 'INVALID_CHECKSUM') {
      throw new CIDError('INVALID_CHECKSUM',
        '❌ *IID con checksum inválido*\nUn dígito está incorrecto.',
        { iid: cleanIid, httpStatus: data.httpStatus, msResponse: data.msResponse });
    }
    if (data.msResponse) {
      const msErr = classifyError(data.msResponse, data.httpStatus, cleanIid);
      if (msErr) throw msErr;
    }

    throw new CIDError('PROXY_ERROR',
      `❌ *Error del servidor de activación*\n${data.message || data.error || 'Sin respuesta válida.'}`,
      { iid: cleanIid, httpStatus: data.httpStatus, msResponse: data });

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
// Función principal — Orquestación de doble respaldo:
// 1. Conexión Directa (TLS Spoofing + DPoP)
// 2. Cloudflare Worker Proxy (si la directa falla por red)
// ============================================================
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

  // INTENTO 1: Conexión directa avanzada
  console.log('[CID] Intento 1: Conexión Directa (TLS Spoofing + DPoP)...');
  try {
    return await getCIDDirect(cleanIid);
  } catch (err) {
    // Errores reales de licencia → no reintentar
    const nonRetryable = ['KEY_BLOCKED', 'TOO_MANY_ACTIVATIONS', 'INVALID_PRODUCT', 'KEY_EXPIRED', 'INVALID_CHECKSUM', 'KEY_NOT_GENUINE'];
    if (nonRetryable.includes(err.code)) throw err;

    console.log(`[CID] Directa falló (${err.code}). Intento 2: Cloudflare Worker Proxy...`);

    // INTENTO 2: Fallback al proxy de Cloudflare
    if (WORKER_PROXY_URL) {
      try {
        return await getCIDViaProxy(cleanIid);
      } catch (proxyErr) {
        console.error('[CID] Proxy también falló:', proxyErr.message);
        // Relanzar el error más descriptivo para el usuario
        if (proxyErr.code && proxyErr.code !== 'PROXY_NETWORK_ERROR') throw proxyErr;
        throw err; // Si ambos fallaron por red, lanzar el primero
      }
    }

    throw err;
  }
}

module.exports = { getConfirmationID, CIDError };
