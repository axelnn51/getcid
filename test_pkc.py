import sys
import xml.etree.ElementTree as ET
import os

# Configurar stdout para soportar emojis en Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

# Simulando lo que hace el docker, en lugar de descargar todo usamos la versión local 'winkeycheck_local'
sys.path.insert(0, os.path.abspath("winkeycheck_local"))

try:
    from licensing_stuff.keycutter import ProductKeyDecoder
    from licensing_stuff.pkeyconfig import PKeyConfig
    from keycheck import query_key, PUB_LICENSE
except ImportError as e:
    print(f"Error importando módulos de winkeycheck: {e}")
    sys.exit(1)

def main():
    pkeyconfig_path = "winkeycheck_local/pkeyconfig.xrm-ms"
    print(f"Cargando {pkeyconfig_path}...")
    
    try:
        with open(pkeyconfig_path, "r", encoding="utf-8-sig") as f:
            pkc = PKeyConfig(ET.fromstring(f.read()))
        print("✅ PKeyConfig cargado exitosamente.")
    except Exception as e:
        print(f"❌ Error cargando PKeyConfig: {e}")
        return
        
    # Clave genérica de Windows 10 Pro
    test_key = "VK7JG-NPHTM-C97JM-9MPGT-3V66T"
    print(f"\nProbando clave: {test_key}")
    
    try:
        pkey_data = ProductKeyDecoder(test_key)
        config = pkc.config_for_group(pkey_data.group)
        
        edition = getattr(config, 'edition_id', None) or getattr(config, 'product_description', None) or str(config.config_id)
        print(f"✅ Edición detectada (offline): {edition}")
        
        print("Consultando online a Microsoft...")
        error_code, message, success = query_key(test_key, pkc)
        if success:
            print(f"✅ Válida! Código: {error_code}")
        else:
            print(f"❌ Inválida/Bloqueada. Código: {error_code}, Mensaje: {message}")
            
    except Exception as e:
        print(f"❌ Error al procesar la clave: {e}")

if __name__ == "__main__":
    main()
