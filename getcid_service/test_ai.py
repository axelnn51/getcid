import os
from dotenv import load_dotenv
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Cargar variables de entorno locales
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))

print(f"Llave GEMINI configurada: {os.getenv('GEMINI_API_KEY')[:10]}...")

try:
    from ai_solver import resolver_captcha_con_ia
    
    image_to_test = "captcha.png"
    if not os.path.exists(image_to_test):
        print(f"Error: No se encontró {image_to_test} para la prueba.")
        sys.exit(1)
        
    print(f"Probando resolver_captcha_con_ia con la imagen: {image_to_test}")
    clicks = resolver_captcha_con_ia(image_to_test)
    
    print("-" * 40)
    print("RESULTADO DE LA PRUEBA:")
    if clicks == -1:
        print("❌ La IA falló al resolver el CAPTCHA.")
    else:
        print(f"✅ ÉXITO: La IA ha determinado que se necesitan dar {clicks} clics hacia la derecha.")
    print("-" * 40)
    
except Exception as e:
    print(f"Error en la prueba: {e}")
