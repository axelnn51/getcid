const crypto = require('crypto');

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

// Función principal para obtener el CID desde el IID.
async function getConfirmationID(iid) {
  if (!iid || iid.length < 54) throw new Error("IID inválido (menos de 54 dígitos).");

  const endpoint = "https://visualsupport.microsoft.com/api/productActivation/validateIID";
  const dpopToken = await generateDPoPToken("/api/productActivation/validateIID", "POST");
  const sid = `app_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
  const digits = Math.floor(iid.length / 9);

  // Timeout de 15 segundos para evitar que el servidor se quede colgado
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
        IID: iid,
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

    // Checksum inválido = OCR leyó mal un dígito
    if (data.validChecksum === false) {
      throw new Error("INVALID_CHECKSUM");
    }

    if (!response.ok) {
      throw new Error(`MS_ERROR_${response.status}`);
    }

    // Microsoft devuelve 'cid' en minúscula
    const cidValue = data.cid || data.CID;

    if (cidValue && typeof cidValue === 'string' && cidValue.length >= 48) {
      return cidValue.match(/\d{6}/g) || cidValue;
    }

    // Si activationSuccessful pero no hay cid en el formato esperado
    if (data.activationSuccessful) {
      return data;
    }

    throw new Error("NO_CID_IN_RESPONSE");

  } catch (err) {
    clearTimeout(timeout);
    if (err.name === 'AbortError') throw new Error("TIMEOUT");
    throw err;
  }
}

module.exports = { getConfirmationID };
