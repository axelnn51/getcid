import imaplib
import email
import re
import asyncio
import logging
from datetime import datetime
import time
import email.utils

logger = logging.getLogger("GmailReader")

async def wait_for_microsoft_code(email_address: str, app_password: str, since_timestamp: datetime, timeout_seconds: int = 90) -> str:
    """
    Connects to Gmail via IMAP, waits for a new email from Microsoft containing a verification code.
    Only considers emails received AFTER `since_timestamp`.
    """
    if not app_password:
        logger.error("No se proporcionó GMAIL_APP_PASSWORD. No se puede leer el correo automáticamente.")
        return None

    # Convertir a timestamp de UNIX para comparación segura
    since_ts = since_timestamp.timestamp()
    start_time = time.time()
    
    logger.info(f"Esperando código de Microsoft en {email_address} (timeout: {timeout_seconds}s)...")
    
    while time.time() - start_time < timeout_seconds:
        try:
            # Nos conectamos en cada iteración para forzar la actualización de INBOX
            mail = imaplib.IMAP4_SSL('imap.gmail.com')
            mail.login(email_address, app_password)
            mail.select('inbox')
            
            # Buscar correos no leídos de microsoft
            status, messages = mail.search(None, '(UNSEEN FROM "microsoft.com")')
            
            if status == 'OK' and messages[0]:
                mail_ids = messages[0].split()
                for mail_id in mail_ids:
                    status, data = mail.fetch(mail_id, '(RFC822)')
                    if status != 'OK':
                        continue
                        
                    for response_part in data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            
                            # Parsear la fecha del correo
                            date_tuple = email.utils.parsedate_tz(msg['Date'])
                            if date_tuple:
                                local_date = datetime.fromtimestamp(email.utils.mktime_tz(date_tuple))
                                email_ts = local_date.timestamp()
                                
                                # Solo procesamos si el correo es NUEVO (después de nuestra solicitud)
                                # Le restamos un margen pequeño (10 segundos) por desincronización de relojes
                                if email_ts >= (since_ts - 10):
                                    # Extraer cuerpo del correo
                                    body = ""
                                    if msg.is_multipart():
                                        for part in msg.walk():
                                            content_type = part.get_content_type()
                                            if content_type in ["text/plain", "text/html"]:
                                                try:
                                                    body += part.get_payload(decode=True).decode(errors='ignore')
                                                except:
                                                    pass
                                    else:
                                        try:
                                            body = msg.get_payload(decode=True).decode(errors='ignore')
                                        except:
                                            pass
                                    
                                    # Buscar código de 7 dígitos. Microsoft usa 7 dígitos.
                                    # Ej: "Código de seguridad: 1234567"
                                    match = re.search(r'\b(\d{7})\b', body)
                                    if match:
                                        code = match.group(1)
                                        logger.info(f"✅ ¡Código nuevo encontrado en IMAP!: {code}")
                                        # Marcar como leído
                                        mail.store(mail_id, '+FLAGS', '\\Seen')
                                        mail.logout()
                                        return code
                                    else:
                                        # Es un correo nuevo de MS pero no tiene código de 7 dígitos.
                                        # Lo marcamos como leído para no re-procesarlo
                                        mail.store(mail_id, '+FLAGS', '\\Seen')
                                else:
                                    # Es un correo viejo que no se había leído. Ignorar.
                                    pass

            mail.logout()
            
        except Exception as e:
            logger.warning(f"Error comprobando IMAP: {e}")
            
        await asyncio.sleep(5)
        
    logger.warning("⏳ Tiempo de espera IMAP agotado. No llegó el código de Microsoft.")
    return None
