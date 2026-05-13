import re
import requests
from typing import Dict, Any, Optional

# ==============================================================================
# CONFIGURACIÓN DE ENDPOINTS Y CABECERAS PRECARGADAS
# ==============================================================================
# Si Microsoft cambia los dominios o rutas de validación en el futuro, 
# actualice las siguientes variables estáticas:
BASE_URL = "https://visualsupport.microsoft.com"
CONFIG_ENDPOINT = f"{BASE_URL}/api/configuration/govUrlID"

# User-Agent estandarizado simulando un dispositivo móvil moderno (iPhone)
# para forzar la vista optimizada / Self Service Mobile.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1"
)


def obtener_cid(iid: str, proxy: Optional[str] = None) -> Dict[str, Any]:
    """
    Realiza la conexión con el portal de activación de Microsoft para obtener
    el Confirmation ID (CID) a partir de un Installation ID (IID).

    :param iid: Cadena numérica de 63 dígitos.
    :param proxy: URL del proxy opcional (ej. 'http://usuario:pass@ip:puerto').
    :return: Diccionario estandarizado con el resultado de la operación.
    """
    # Limpiar el IID para asegurar que contenga únicamente caracteres numéricos
    clean_iid = re.sub(r"\D", "", iid)
    if len(clean_iid) < 54 or len(clean_iid) > 63:
        return {
            "status": "error",
            "error_type": "INVALID_IID",
            "message": f"La longitud del IID es incorrecta ({len(clean_iid)} dígitos detectados)."
        }

    # 1. MANEJO DE SESIÓN Y EMULACIÓN
    session = requests.Session()
    session.headers.update({
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json, text/html, application/xhtml+xml, application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
    })

    # Configurar proxies si se proveen
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})

    try:
        # Paso 1: Petición GET inicial para capturar cookies de sesión y tokens de ruteo
        # (Frecuentemente el portal asigna un govUrlID o Session ID dinámico)
        config_resp = session.get(CONFIG_ENDPOINT, timeout=15)
        config_resp.raise_for_status()
        
        config_data = config_resp.json()
        gov_url_id = config_data.get("govUrlID")
        
        if not gov_url_id:
            return {
                "status": "error",
                "error_type": "SESSION_INIT_FAILED",
                "message": "No se pudo capturar el identificador dinámico de sesión del portal."
            }

        # Construir URL de destino dinámico para el POST de activación
        target_url = f"{BASE_URL}/{gov_url_id}"

        # Realizar GET a la URL de bienvenida para consolidar cookies (CSRF / Session Handshake)
        session.get(target_url, timeout=15)

        # 2. PROCESAMIENTO Y PETICIÓN POST
        # Dividir el IID en los bloques requeridos por el formulario web (típicamente de 6 o 7 dígitos)
        block_size = 6 if len(clean_iid) <= 54 else 7
        blocks = [clean_iid[i:i+block_size] for i in range(0, len(clean_iid), block_size)]
        
        # Estructurar el payload adaptado al esquema del formulario web móvil
        payload = {
            "installationIdBlocks": blocks,
            "productType": "Windows" if block_size == 6 else "Office",
            "completeIID": clean_iid
        }

        # Realizar la petición POST con los datos de activación
        post_headers = {
            "Referer": target_url,
            "Content-Type": "application/json"
        }
        
        # Nota: Dependiendo del endpoint exacto de validación (ej. /submit o la misma URL),
        # ajustar la ruta de envío POST a continuación:
        submit_url = f"{target_url}/api/activate"  # Ruta de ejemplo basada en APIs móviles estandarizadas
        
        resp = session.post(submit_url, json=payload, headers=post_headers, timeout=20)
        
        # 4. MANEJO DE ERRORES COMUNES DEL SERVIDOR
        # Inspeccionar códigos de estado HTTP comunes para límites o bloqueos
        if resp.status_code == 403:
            return {
                "status": "error",
                "error_type": "ACCESS_DENIED",
                "message": "Acceso denegado (403). La solicitud fue bloqueada por el firewall o el IID no es válido en este portal."
            }
        elif resp.status_code == 429:
            return {
                "status": "error",
                "error_type": "RATE_LIMIT_EXCEEDED",
                "message": "Demasiadas solicitudes. Espere unos minutos antes de reintentar."
            }
            
        resp_text = resp.text.lower()
        
        # Validar respuestas de rechazo dentro del contenido (HTML o JSON devuelto)
        if "exceeded" in resp_text or "límite" in resp_text or "too many" in resp_text:
            return {
                "status": "error",
                "error_type": "ACTIVATION_LIMIT_EXCEEDED",
                "message": "Límite de activaciones excedido para esta clave."
            }
        elif "blocked" in resp_text or "bloqueada" in resp_text:
            return {
                "status": "error",
                "error_type": "KEY_BLOCKED",
                "message": "La clave de producto ha sido bloqueada por el servidor."
            }
        elif "not genuine" in resp_text or "invalid" in resp_text or "no válida" in resp_text:
            return {
                "status": "error",
                "error_type": "INVALID_IID",
                "message": "El IID no es válido o no corresponde a un producto elegible."
            }

        resp.raise_for_status()

        # 3. EXTRACCIÓN (SCRAPING) Y RETORNO
        # Intentar extraer bloques del Confirmation ID (típicamente 8 bloques de 6 dígitos = 48 dígitos)
        # mediante parseo directo usando expresiones regulares sobre la respuesta
        cid_matches = re.findall(r"\b\d{6}\b", resp.text)
        
        # Si la respuesta entrega suficientes bloques numéricos, ensamblar el CID
        if len(cid_matches) >= 8:
            cid_final = "-".join(cid_matches[:8])
            return {
                "status": "success",
                "cid": cid_final,
                "raw_blocks": cid_matches[:8]
            }
            
        # Alternativamente, si el servidor devuelve un JSON directo con el CID:
        try:
            json_data = resp.json()
            if "confirmationId" in json_data:
                return {
                    "status": "success",
                    "cid": json_data["confirmationId"]
                }
        except Exception:
            pass

        # Si no se localiza el CID en la respuesta exitosa
        return {
            "status": "error",
            "error_type": "CID_NOT_FOUND",
            "message": "La solicitud fue procesada pero no se encontró un Confirmation ID en la respuesta."
        }

    except requests.exceptions.ProxyError as e:
        return {
            "status": "error",
            "error_type": "PROXY_ERROR",
            "message": f"Fallo en la conexión del proxy: {str(e)}"
        }
    except requests.exceptions.RequestException as e:
        return {
            "status": "error",
            "error_type": "NETWORK_ERROR",
            "message": f"Error de comunicación HTTP: {str(e)}"
        }
    except Exception as e:
        return {
            "status": "error",
            "error_type": "INTERNAL_ERROR",
            "message": f"Error inesperado procesando la petición: {str(e)}"
        }


# Ejemplo de uso local para pruebas aisladas
if __name__ == "__main__":
    # IID de demostración
    iid_demo = "1234567-1234567-1234567-1234567-1234567-1234567-1234567-1234567-1234567"
    resultado = obtener_cid(iid_demo)
    print(resultado)
