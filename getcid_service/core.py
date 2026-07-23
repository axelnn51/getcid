import httpx
import uuid
import time
import json
import base64
import hashlib
import logging
from typing import Dict
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
import jwt
import re
from scraper import extract_ms_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("GetCID_Core")

private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
public_numbers = private_key.public_key().public_numbers()

def int_to_base64url(i: int) -> str:
    b = i.to_bytes((i.bit_length() + 7) // 8, byteorder='big')
    return base64.urlsafe_b64encode(b).decode('utf-8').rstrip('=')

jwk = {
    "crv": "P-256",
    "kty": "EC",
    "x": int_to_base64url(public_numbers.x),
    "y": int_to_base64url(public_numbers.y)
}

canonical_jwk = json.dumps(jwk, separators=(',', ':')).encode('utf-8')
jkt_hash = hashlib.sha256(canonical_jwk).digest()
jkt = base64.urlsafe_b64encode(jkt_hash).decode('utf-8').rstrip('=')

def generate_dpop_token(htu: str, htm: str, nonce: str = None) -> str:
    header = {"alg": "ES256", "typ": "dpop+jwt", "jwk": jwk}
    payload = {
        "htu": htu,
        "htm": htm,
        "jti": str(uuid.uuid4()),
        "iat": int(time.time()),
        "jkt": jkt
    }
    if nonce:
        payload["nonce"] = nonce

    token = jwt.encode(payload, private_key, algorithm="ES256", headers=header)
    return token

async def process_iid(iid: str, ms_session_token: str = None) -> Dict[str, str]:
    iid = iid.replace(" ", "").replace("-", "")
    
    if len(iid) not in [54, 63] or not iid.isdigit():
        return {"success": False, "error": "El IID debe tener exactamente 54 o 63 dígitos numéricos."}

    if not ms_session_token:
        logger.info("Token manual no provisto. Iniciando scraper para obtener token...")
        ms_session_token = await extract_ms_token()
        if not ms_session_token:
            return {"success": False, "error": "No se pudo obtener el token de sesión de Microsoft."}

    endpoint = "https://visualsupport.microsoft.com/api/productActivation/validateIID"
    htu = endpoint  # DPoP RFC 9449: htu MUST be the full URL (scheme + host + path)
    htm = "POST"
    
    sid = f"app_{int(time.time() * 1000)}_{str(uuid.uuid4())[:8]}"
    digits = len(iid) // 9
    
    payload_data = {
        "IID": iid,
        "ProductType": "windows",
        "productGroup": "Windows",
        "productName": "Windows 11",
        "numberOfDigits": digits,
        "Country": "CHN",
        "Region": "APAC",
        "InstalledDevices": 1,
        "OverrideStatusCode": "MUL",
        "InitialReasonCode": "45164"
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ms_session_token}",
        "x-session-id": sid,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Origin": "https://visualsupport.microsoft.com",
        "Referer": "https://visualsupport.microsoft.com/"
    }

    try:
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            dpop = generate_dpop_token(htu, htm)
            req_headers = headers.copy()
            req_headers["DPoP"] = dpop
            
            logger.info(f"[{iid}] Iniciando desafío DPoP con Microsoft...")
            resp = await client.post(endpoint, json=payload_data, headers=req_headers)
            
            if "dpop-nonce" in resp.headers or "DPoP-Nonce" in resp.headers:
                nonce = resp.headers.get("dpop-nonce", resp.headers.get("DPoP-Nonce"))
                logger.info(f"[{iid}] Nonce detectado, reintentando con firma completa...")
                req_headers["DPoP"] = generate_dpop_token(htu, htm, nonce)
                resp = await client.post(endpoint, json=payload_data, headers=req_headers)
            
            if resp.status_code in (401, 403):
                # 401 Unauthorized y 403 Forbidden ambos indican token expirado/inválido.
                # El mensaje "Token expirado" es el que main.py busca para disparar renovación automática.
                return {"success": False, "error": "Token expirado o Denegado."}
            elif resp.status_code != 200:
                return {"success": False, "error": f"Error: {resp.status_code}"}
                
            data = resp.json()
            cid_value = data.get("cid") or data.get("CID") or data.get("confirmationId")
            
            if cid_value and isinstance(cid_value, str) and len(cid_value) >= 48:
                formatted_cid = "-".join(re.findall(r'.{6}', cid_value)) if "-" not in cid_value else cid_value
                return {"success": True, "cid": formatted_cid, "raw_cid": cid_value}
                
            if data.get("validChecksum") is False:
                return {"success": False, "error": "IID con checksum inválido."}
                
            return {"success": False, "error": f"Respuesta inesperada: {data}"}

    except Exception as e:
        return {"success": False, "error": f"Error interno: {str(e)}"}
