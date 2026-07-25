import httpx
import uuid
import time
import json
import base64
import hashlib
import logging
from typing import Dict
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import utils
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
import jwt
import re
from scraper import extract_ms_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("GetCID_Core")

def _load_or_generate_key():
    import os
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.backends import default_backend
    key_path = "/app/persist/ms_dpop_key.json"
    if not os.path.exists(key_path):
        key_path = "ms_dpop_key.json"
        
    if os.path.exists(key_path):
        try:
            with open(key_path, "r") as f:
                jwk_data = json.load(f)
            if jwk_data.get("kty") == "EC":
                # Convert base64url to int
                def b64url_to_int(s):
                    s = s + "=" * (4 - len(s) % 4)
                    return int.from_bytes(base64.urlsafe_b64decode(s), "big")
                d = b64url_to_int(jwk_data["d"])
                x = b64url_to_int(jwk_data["x"])
                y = b64url_to_int(jwk_data["y"])
                
                pn = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1())
                private_numbers = ec.EllipticCurvePrivateNumbers(d, pn)
                pk = private_numbers.private_key(default_backend())
                logger.info("✅ Clave DPoP de MSAL cargada exitosamente.")
                return pk, jwk_data
            elif jwk_data.get("kty") == "RSA":
                def b64url_to_int(s):
                    s = s + "=" * (4 - len(s) % 4)
                    return int.from_bytes(base64.urlsafe_b64decode(s), "big")
                d = b64url_to_int(jwk_data["d"])
                p = b64url_to_int(jwk_data["p"])
                q = b64url_to_int(jwk_data["q"])
                dp = b64url_to_int(jwk_data["dp"])
                dq = b64url_to_int(jwk_data["dq"])
                qi = b64url_to_int(jwk_data["qi"])
                e = b64url_to_int(jwk_data["e"])
                n = b64url_to_int(jwk_data["n"])
                pn = rsa.RSAPublicNumbers(e, n)
                private_numbers = rsa.RSAPrivateNumbers(p, q, d, dp, dq, qi, pn)
                pk = private_numbers.private_key(default_backend())
                logger.info("✅ Clave DPoP de MSAL (RSA) cargada exitosamente.")
                return pk, jwk_data
        except Exception as e:
            logger.warning(f"⚠️ Error cargando DPoP key de MSAL: {e}. Generando nueva...")

    pk = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    pub = pk.public_key().public_numbers()
    def int_to_base64url(i: int) -> str:
        b = i.to_bytes((i.bit_length() + 7) // 8, byteorder='big')
        if not b: b = b'\x00'
        return base64.urlsafe_b64encode(b).decode('utf-8').rstrip('=')
    gen_jwk = {
        "e": int_to_base64url(pub.e),
        "kty": "RSA",
        "n": int_to_base64url(pub.n)
    }
    return pk, gen_jwk

private_key, jwk = _load_or_generate_key()

canonical_jwk = json.dumps(jwk, separators=(',', ':')).encode('utf-8')
jkt_hash = hashlib.sha256(canonical_jwk).digest()
jkt = base64.urlsafe_b64encode(jkt_hash).decode('utf-8').rstrip('=')

def generate_dpop_token(htu: str, htm: str, nonce: str = None, access_token: str = None) -> str:
    alg = "RS256" if jwk.get("kty") == "RSA" else "ES256"
    header = {"alg": alg, "typ": "dpop+jwt", "jwk": jwk}
    payload = {
        "htu": htu,
        "htm": htm,
        "jti": str(uuid.uuid4()),
        "iat": int(time.time())
    }
    if nonce:
        payload["nonce"] = nonce
    if access_token:
        ath_hash = hashlib.sha256(access_token.encode('ascii')).digest()
        payload["ath"] = base64.urlsafe_b64encode(ath_hash).decode('utf-8').rstrip('=')

    token = jwt.encode(payload, private_key, algorithm=alg, headers=header)
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
    htu = endpoint
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
        "Authorization": f"DPoP {ms_session_token}",
        "x-session-id": sid,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Origin": "https://visualsupport.microsoft.com",
        "Referer": "https://visualsupport.microsoft.com/"
    }

    try:
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            dpop = generate_dpop_token(htu, htm, access_token=ms_session_token)
            req_headers = headers.copy()
            req_headers["DPoP"] = dpop
            
            logger.info(f"[{iid}] Iniciando desafío DPoP con Microsoft...")
            resp = await client.post(endpoint, json=payload_data, headers=req_headers)
            
            if "dpop-nonce" in resp.headers or "DPoP-Nonce" in resp.headers:
                nonce = resp.headers.get("dpop-nonce", resp.headers.get("DPoP-Nonce"))
                logger.info(f"[{iid}] Nonce detectado, reintentando con firma completa...")
                req_headers["DPoP"] = generate_dpop_token(htu, htm, nonce, access_token=ms_session_token)
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
