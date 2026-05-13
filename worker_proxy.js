// ============================================================
// Cloudflare Worker — Proxy CID para GETCID
// Deploy: wrangler deploy
// Este worker actúa como intermediario entre tu bot/web y
// la API de Microsoft para obtener Confirmation IDs.
// Microsoft bloquea peticiones desde IPs de VPS/residenciales
// pero acepta desde edge nodes de Cloudflare.
// ============================================================

// Base64URL
function eI(t) {
  let e = t instanceof ArrayBuffer ? new Uint8Array(t) : new TextEncoder().encode(t);
  let n = "";
  for (let o of e) n += String.fromCharCode(o);
  return btoa(n).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// ECDSA key cache
let tI = null;
async function yT() {
  if (!tI) {
    tI = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  }
  return tI;
}

// Generate DPoP Token
async function generateDPoP(htu, htm) {
  const { privateKey, publicKey } = await yT();
  const jwk = await crypto.subtle.exportKey("jwk", publicKey);
  const header = { alg: "ES256", typ: "dpop+jwt", jwk };
  const payload = { htu, htm, jti: crypto.randomUUID(), iat: Math.floor(Date.now() / 1000) };
  const s = eI(JSON.stringify(header));
  const l = eI(JSON.stringify(payload));
  const u = `${s}.${l}`;
  const p = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, privateKey, new TextEncoder().encode(u));
  return `${u}.${eI(p)}`;
}

// Send activation request to Microsoft
async function sendActivationRequest(IID) {
  const dpopToken = await generateDPoP("/api/productActivation/validateIID", "POST");
  const sid = `app_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
  const digits = Math.floor(IID.length / 9);

  const resp = await fetch("https://visualsupport.microsoft.com/api/productActivation/validateIID", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": "Bearer govUrlID",
      "DPoP": dpopToken,
      "x-session-id": sid
    },
    body: JSON.stringify({
      IID,
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

  const data = await resp.json().catch(() => ({ raw: "parse_error" }));
  return { status: resp.status, success: resp.ok, data };
}

// CORS headers
const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
  "Content-Type": "application/json"
};

export default {
  async fetch(request, env) {
    // Handle CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    // Only allow POST
    if (request.method !== "POST") {
      return new Response(JSON.stringify({ error: "Method not allowed" }), {
        status: 405, headers: corsHeaders
      });
    }

    // API Key validation (set WORKER_API_KEY in worker settings)
    const apiKey = request.headers.get("X-API-Key") || "";
    const expectedKey = env.WORKER_API_KEY || "";
    if (expectedKey && apiKey !== expectedKey) {
      return new Response(JSON.stringify({ error: "Unauthorized" }), {
        status: 401, headers: corsHeaders
      });
    }

    try {
      const body = await request.json();
      const iid = (body.iid || body.IID || "").replace(/\D/g, "");

      if (!iid || iid.length < 54 || iid.length > 63) {
        return new Response(JSON.stringify({ 
          error: "Invalid IID", 
          detail: `IID must be 54-63 digits, got ${iid.length}` 
        }), { status: 400, headers: corsHeaders });
      }

      const result = await sendActivationRequest(iid);

      // Extract CID if present
      const cidValue = result.data?.cid || result.data?.CID;
      if (cidValue && typeof cidValue === "string" && cidValue.length >= 48) {
        const cidBlocks = cidValue.match(/\d{6}/g);
        return new Response(JSON.stringify({
          success: true,
          iid: iid,
          cid: cidBlocks || cidValue,
          cidRaw: cidValue,
          validChecksum: result.data.validChecksum,
          httpStatus: result.status
        }), { status: 200, headers: corsHeaders });
      }

      // Checksum invalid
      if (result.data?.validChecksum === false) {
        return new Response(JSON.stringify({
          success: false,
          error: "INVALID_CHECKSUM",
          message: "IID checksum is invalid. A digit may be incorrect.",
          iid: iid,
          httpStatus: result.status
        }), { status: 400, headers: corsHeaders });
      }

      // Activation successful but unusual CID format
      if (result.data?.activationSuccessful) {
        return new Response(JSON.stringify({
          success: true,
          iid: iid,
          data: result.data,
          httpStatus: result.status
        }), { status: 200, headers: corsHeaders });
      }

      // Error from Microsoft
      return new Response(JSON.stringify({
        success: false,
        error: "MS_ERROR",
        httpStatus: result.status,
        iid: iid,
        msResponse: result.data
      }), { status: result.status >= 400 ? result.status : 400, headers: corsHeaders });

    } catch (err) {
      return new Response(JSON.stringify({ 
        error: "Internal error", 
        message: err.message 
      }), { status: 500, headers: corsHeaders });
    }
  }
};
