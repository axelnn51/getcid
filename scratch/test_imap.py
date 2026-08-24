import imaplib
import email
from email.header import decode_header

username = "axelnn56@gmail.com"
password = "mibohgbdvvufntis"

try:
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(username, password)
    print("IMAP Login successful!")
    mail.select("inbox")
    status, messages = mail.search(None, 'FROM', '"Microsoft account team"')
    print(f"Status: {status}, Messages found: {len(messages[0].split())}")
    mail.logout()
except Exception as e:
    print(f"IMAP Error: {e}")
