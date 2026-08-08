import time
import base64
import json
import uuid
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization

class DPoPEngine:
    def __init__(self):
        # Generar una clave privada ES256 (P-256)
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.public_key = self.private_key.public_key()
        
    def _get_jwk(self):
        # Exportar la clave pública en formato JWK
        public_numbers = self.public_key.public_numbers()
        x_bytes = public_numbers.x.to_bytes(32, byteorder='big')
        y_bytes = public_numbers.y.to_bytes(32, byteorder='big')
        
        return {
            "kty": "EC",
            "crv": "P-256",
            "x": self._base64url_encode(x_bytes),
            "y": self._base64url_encode(y_bytes)
        }

    def _base64url_encode(self, data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')

    def generate_dpop_proof(self, method: str, url: str, nonce: str = None) -> str:
        """
        Genera el token DPoP requerido por Microsoft para las peticiones HTTP.
        """
        jwk = self._get_jwk()
        
        header = {
            "typ": "dpop+jwt",
            "alg": "ES256",
            "jwk": jwk
        }
        
        payload = {
            "jti": str(uuid.uuid4()),
            "htm": method.upper(),
            "htu": url,
            "iat": int(time.time())
        }
        
        if nonce:
            payload["nonce"] = nonce

        
        # PyJWT maneja la firma usando la clave privada de cryptography
        # Solo necesitamos pasar la clave en formato PEM
        private_pem = self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        
        dpop_token = jwt.encode(
            payload,
            private_pem,
            algorithm="ES256",
            headers=header
        )
        
        return dpop_token
