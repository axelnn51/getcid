import asyncio
import sys
import time

sys.path.append('.')
from device_auth import start_device_code_flow, _active_flow, _poll_once
from core import process_iid
from token_refresher import TOKEN_CACHE_FILE
import json

async def wait_for_device_code(flow_result):
    print(f"\n" + "="*60)
    print(f"POR FAVOR, INICIA SESION PARA AUTORIZAR EL SERVIDOR")
    print(f"1. Abre esta URL en tu navegador: {flow_result['verification_uri']}")
    print(f"2. Ingresa este codigo: {flow_result['user_code']}")
    print(f"="*60 + "\n")
    print("El servidor esta esperando a que completes el inicio de sesion...")
    
    interval = 5
    max_wait = time.time() + 900
    
    while time.time() < max_wait:
        result = await _poll_once()
        if result.get("success"):
            print("\nExito! Token obtenido exitosamente via Device Code Flow!")
            return True
            
        status = result.get("status", "")
        if status == "pending":
            await asyncio.sleep(interval)
            continue
            
        print(f"\nEl flujo fallo: {result.get('error', 'Desconocido')}")
        return False
        
    print("\nTiempo de espera agotado.")
    return False

async def main():
    print("Iniciando flujo de Device Auth para obtener token real...")
    flow_result = await start_device_code_flow(1) # Usar Microsoft Office client ID
    
    if not flow_result.get("success"):
        print(f"Error iniciando flujo: {flow_result.get('error')}")
        return
        
    success = await wait_for_device_code(flow_result)
    
    if success:
        # Leer el token recién guardado
        with open(TOKEN_CACHE_FILE, "r") as f:
            data = json.load(f)
            token = data["token"]
            
        iids = [
            "002276462920723863992464380600853415068723590669307858808651841",
            "242597324534335861345900255084321326992512623551262305624134323"
        ]
        
        print("\n" + "*"*60)
        print("INICIANDO PROCESAMIENTO DE IIDs REALES")
        print("*"*60)
        
        for idx, iid in enumerate(iids):
            print(f"\nProcesando IID {idx+1}: {iid}")
            result = await process_iid(iid, ms_session_token=token)
            print(f"Resultado: {result}")
            
        print("\nPruebas finalizadas.")

if __name__ == "__main__":
    asyncio.run(main())
