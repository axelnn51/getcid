"""
GETCID — Batch API CID Retrieval
Adapted from PKeyMaster (https://github.com/ntriver-org/PKeyMaster) — Unlicense
Implements Microsoft's BatchActivation SOAP endpoint for CID retrieval,
with fallback to the Visual API using a shared community token.
"""

import hashlib
import hmac
import base64
import re
import logging
import xml.etree.ElementTree as ET

import httpx

logger = logging.getLogger("BatchCID")

# ============================================================
# Constants from PKeyMaster source (GetCidBatchApi.ps1)
# ============================================================

BATCH_URL = "https://activation.sls.microsoft.com/BatchActivation/BatchActivation.asmx"
SOAP_ACTION = "http://www.microsoft.com/BatchActivationService/BatchActivate"
DEFAULT_PID = "00000-00138-207-109016-00-1033-26100.0000-0922026"
USER_AGENT_BATCH = "Mozilla/4.0 (compatible; MSIE 6.0; MS Web Services Client Protocol 2.0.50727.5420)"

# HMAC-SHA256 key (from PKeyMaster Get-BatchActivationHashKey)
HMAC_KEY = bytes([
    254, 49, 152, 117, 251, 72, 132, 134, 156, 243, 241, 206, 153, 168, 144, 100,
    171, 87, 31, 202, 71, 4, 80, 88, 48, 36, 226, 20, 98, 135, 121, 160
])

# Visual API fallback constants
VISUAL_URL = "https://visualsupport.microsoft.com/api/productActivation/validateIID"
TOKEN_SERVER = "https://cidtoken.ntriver.org/token.json"
USER_AGENT_VISUAL = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# Error code mapping from PKeyMaster
BATCH_ERROR_CODES = {
    "0x67": "La clave ha sido bloqueada",
    "0xD5": "Límite de activación ROT alcanzado",
    "0x68": "Clave no soportada o generada",
    "0x71": "La clave excedió su límite de activaciones",
    "0x7F": "La clave MAK excedió su límite de activaciones",
    "0xD6": "Límite de activación DMAK alcanzado",
    "0x86": "La clave es válida pero su tipo no es soportado",
    "0x90": "Installation ID inválido",
    "0xC004C017": "La clave ha sido bloqueada para esta ubicación geográfica",
    "0x80131600": "AdvancedPid inválido o error del servidor",
}


def _build_soap_request(iid: str, pid: str = DEFAULT_PID) -> str:
    """Build the SOAP envelope with HMAC-SHA256 signed RequestXml."""

    # Inner XML (the actual activation request)
    request_inner = (
        '<ActivationRequest xmlns="http://www.microsoft.com/DRM/SL/BatchActivationRequest/1.0">\r\n'
        "  <VersionNumber>2.0</VersionNumber>\r\n"
        "  <RequestType>1</RequestType>\r\n"
        "  <Requests>\r\n"
        f"    <Request><PID>{pid}</PID><IID>{iid}</IID></Request>\r\n"
        "  </Requests>\r\n"
        "</ActivationRequest>"
    )

    # Encode as UTF-16 LE (Unicode) — matches PowerShell's [Text.Encoding]::Unicode
    xml_bytes = request_inner.encode("utf-16-le")

    # HMAC-SHA256 digest
    digest = base64.b64encode(
        hmac.new(HMAC_KEY, xml_bytes, hashlib.sha256).digest()
    ).decode("ascii")

    # Base64 encode the request XML
    req64 = base64.b64encode(xml_bytes).decode("ascii")

    # Full SOAP envelope
    soap = (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">\r\n'
        "  <soap:Body>\r\n"
        '    <BatchActivate xmlns="http://www.microsoft.com/BatchActivationService">\r\n'
        "      <request>\r\n"
        f"        <Digest>{digest}</Digest>\r\n"
        f"        <RequestXml>{req64}</RequestXml>\r\n"
        "      </request>\r\n"
        "    </BatchActivate>\r\n"
        "  </soap:Body>\r\n"
        "</soap:Envelope>"
    )

    return soap


def _parse_batch_response(response_text: str) -> dict:
    """Parse the SOAP response and extract CID or error."""
    result = {"success": False, "cid": None, "error_code": None, "error_message": None}

    try:
        root = ET.fromstring(response_text)
    except ET.ParseError as e:
        result["error_message"] = f"Error parseando XML: {e}"
        return result

    # Find ResponseXml node (contains Base64-encoded inner XML)
    # Use local-name() approach for namespace-agnostic search
    response_xml_node = None
    for elem in root.iter():
        if elem.tag.endswith("ResponseXml") and elem.text:
            response_xml_node = elem
            break

    if response_xml_node is None:
        result["error_message"] = "Formato de respuesta SOAP inesperado"
        return result

    # The ResponseXml text IS the inner XML directly (not Base64 in the response)
    inner_text = response_xml_node.text.strip()

    try:
        inner_root = ET.fromstring(inner_text)
    except ET.ParseError:
        result["error_message"] = "Error parseando XML interno de respuesta"
        return result

    # Look for CID node
    for elem in inner_root.iter():
        if elem.tag.endswith("CID") and elem.text:
            result["success"] = True
            result["cid"] = elem.text.strip()
            return result

    # Look for ErrorCode node
    for elem in inner_root.iter():
        if elem.tag.endswith("ErrorCode") and elem.text:
            error_code = elem.text.strip()
            result["error_code"] = error_code
            result["error_message"] = BATCH_ERROR_CODES.get(
                error_code, f"Error del servidor ({error_code})"
            )
            return result

    result["error_message"] = "Nodo CID o ErrorCode no encontrado en la respuesta"
    return result


async def _get_cid_batch(iid: str) -> dict:
    """Try to get CID via Batch API (SOAP/XML)."""
    soap_body = _build_soap_request(iid)

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": SOAP_ACTION,
        "User-Agent": USER_AGENT_BATCH,
    }

    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        response = await client.post(BATCH_URL, content=soap_body, headers=headers)

    if response.status_code != 200:
        return {
            "success": False,
            "cid": None,
            "error_code": str(response.status_code),
            "error_message": f"HTTP {response.status_code} del servidor de Microsoft",
        }

    return _parse_batch_response(response.text)


async def _get_shared_token() -> str | None:
    """Fetch shared Bearer token from ntriver community server."""
    try:
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            resp = await client.get(TOKEN_SERVER)
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("token") or data.get("access_token")
                if token and isinstance(token, str) and len(token) > 50:
                    return token
    except Exception as e:
        logger.warning(f"No se pudo obtener token compartido: {e}")
    return None


async def _get_cid_visual(iid: str) -> dict:
    """Fallback: Try to get CID via Visual API with shared token."""
    result = {"success": False, "cid": None, "error_code": None, "error_message": None}

    token = await _get_shared_token()
    if not token:
        result["error_message"] = "No se pudo obtener token compartido para Visual API"
        return result

    # Build DPoP proof (simple ephemeral key)
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        import jwt as pyjwt
        import uuid
        import time

        private_key = ec.generate_private_key(ec.SECP256R1())
        public_numbers = private_key.public_key().public_numbers()

        def b64url(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

        jwk = {
            "kty": "EC",
            "crv": "P-256",
            "x": b64url(public_numbers.x.to_bytes(32, "big")),
            "y": b64url(public_numbers.y.to_bytes(32, "big")),
        }

        dpop_header = {"alg": "ES256", "typ": "dpop+jwt", "jwk": jwk}
        dpop_payload = {
            "htu": "/api/productActivation/validateIID",
            "htm": "POST",
            "jti": str(uuid.uuid4()),
            "iat": int(time.time()),
        }

        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        dpop_proof = pyjwt.encode(dpop_payload, private_pem, algorithm="ES256", headers=dpop_header)

    except ImportError:
        result["error_message"] = "Dependencias de Visual API no disponibles (cryptography/PyJWT)"
        return result

    digits = len(iid) // 9
    payload = {
        "IID": iid,
        "ProductType": "windows",
        "productGroup": "Windows",
        "productName": "Windows 11",
        "numberOfDigits": digits,
        "Country": "USA",
        "Region": "NOAM",
        "InstalledDevices": 1,
        "OverrideStatusCode": "MUL",
        "InitialReasonCode": "45164",
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "DPoP": dpop_proof,
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": USER_AGENT_VISUAL,
        "x-session-id": f"app_{uuid.uuid4().hex[:32]}",
    }

    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        resp = await client.post(VISUAL_URL, json=payload, headers=headers)

        # Handle DPoP nonce challenge
        nonce = resp.headers.get("dpop-nonce") or resp.headers.get("DPoP-Nonce")
        if nonce:
            dpop_payload["nonce"] = nonce
            dpop_payload["jti"] = str(uuid.uuid4())
            dpop_payload["iat"] = int(time.time())
            headers["DPoP"] = pyjwt.encode(dpop_payload, private_pem, algorithm="ES256", headers=dpop_header)
            resp = await client.post(VISUAL_URL, json=payload, headers=headers)

    if resp.status_code != 200:
        result["error_message"] = f"Visual API HTTP {resp.status_code}"
        return result

    data = resp.json()
    cid_value = data.get("cid")

    if cid_value and isinstance(cid_value, str) and len(cid_value) >= 40:
        result["success"] = True
        result["cid"] = cid_value
        return result

    reason = data.get("reasonCode")
    message = data.get("message", "Respuesta desconocida")
    result["error_code"] = reason
    result["error_message"] = message
    return result


async def get_cid(iid: str) -> dict:
    """
    Main entry point: Get CID for an Installation ID.
    Tries Batch API first, falls back to Visual API.
    Returns dict with keys: success, cid, formatted_cid, error_code, error_message, method
    """
    # Clean IID
    clean_iid = re.sub(r"\D", "", iid)

    if len(clean_iid) not in (54, 63):
        return {
            "success": False,
            "cid": None,
            "formatted_cid": None,
            "error_message": f"IID debe tener 54 o 63 dígitos (recibido: {len(clean_iid)})",
            "method": None,
        }

    # --- Try Batch API first (most stable, no tokens needed) ---
    logger.info(f"[{clean_iid[:12]}...] Intentando Batch API...")
    batch_result = {}
    try:
        batch_result = await _get_cid_batch(clean_iid)
        if batch_result["success"]:
            cid = batch_result["cid"]
            formatted = "-".join(re.findall(r".{6}", cid)) if "-" not in cid else cid
            logger.info(f"[{clean_iid[:12]}...] ✅ CID obtenido via Batch API")
            return {
                "success": True,
                "cid": cid,
                "formatted_cid": formatted,
                "error_message": None,
                "method": "batch_api",
            }
        logger.warning(f"[{clean_iid[:12]}...] Batch API falló: {batch_result.get('error_message')}")
    except Exception as e:
        logger.error(f"[{clean_iid[:12]}...] Excepción en Batch API: {e}")
        batch_result = {"error_code": None, "error_message": f"Excepción interna: {e}"}

    # --- Fallback: Visual API with shared token ---
    logger.info(f"[{clean_iid[:12]}...] Intentando Visual API (fallback)...")
    visual_result = {}
    try:
        visual_result = await _get_cid_visual(clean_iid)
        if visual_result["success"]:
            cid = visual_result["cid"]
            formatted = "-".join(re.findall(r".{6}", cid)) if "-" not in cid else cid
            logger.info(f"[{clean_iid[:12]}...] ✅ CID obtenido via Visual API")
            return {
                "success": True,
                "cid": cid,
                "formatted_cid": formatted,
                "error_message": None,
                "method": "visual_api",
            }
        logger.warning(f"[{clean_iid[:12]}...] Visual API falló: {visual_result['error_message']}")
    except Exception as e:
        logger.error(f"[{clean_iid[:12]}...] Excepción en Visual API: {e}")
        visual_result = {"error_code": None, "error_message": str(e)}

    # --- Both failed ---
    # Return the most informative error (prefer batch if it had a specific code)
    error_msg = batch_result.get("error_message") or visual_result.get("error_message") or "Error desconocido"
    error_code = batch_result.get("error_code") or visual_result.get("error_code")

    return {
        "success": False,
        "cid": None,
        "formatted_cid": None,
        "error_code": error_code,
        "error_message": error_msg,
        "method": None,
    }
