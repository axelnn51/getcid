import asyncio
from playwright.async_api import async_playwright
import json
import time

async def run():
    async with async_playwright() as p:
        print("\n" + "="*50)
        print("INICIANDO CAPTURA MANUAL DE TOKEN")
        print("="*50)
        
        # Lanzamos el navegador
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={'width': 1280, 'height': 720})
        page = await context.new_page()
        
        print("\n1. Se abrira una ventana de Chrome.")
        print("2. Inicia sesion normalmente en visualsupport.microsoft.com")
        print("3. Resuelve el CAPTCHA si aparece (hazlo con calma).")
        print("4. Quedate en la pagina hasta que veas el mensaje de exito aqui.\n")
        
        await page.goto("https://visualsupport.microsoft.com")
        
        token_data = {}
        
        async def handle_request(request):
            # Buscamos la peticion de token de Microsoft
            if "token" in request.url and request.method == "POST":
                try:
                    response = await request.response()
                    if response and response.status == 200:
                        data = await response.json()
                        if "refresh_token" in data:
                            token_data["refresh_token"] = data["refresh_token"]
                            token_data["client_id"] = "2b217cec-607d-4eb6-887e-c928520a14f6"
                            token_data["scopes"] = "openid profile email"
                            token_data["email"] = "axelnn52@outlook.com"
                            token_data["saved_at"] = time.time()
                            print("\n" + "*"*50)
                            print("¡TOKEN CAPTURADO CON EXITO!")
                            print("*"*50)
                except:
                    pass

        page.on("requestfinished", lambda req: asyncio.create_task(handle_request(req)))

        # Esperamos a capturar o a que el usuario cierre el navegador
        try:
            while "refresh_token" not in token_data:
                await asyncio.sleep(1)
                # Si el navegador se cierra manualmente antes de capturar
                if page.is_closed():
                    break
        except Exception:
            pass
        
        if "refresh_token" in token_data:
            with open("ms_refresh_token_NUEVO.json", "w") as f:
                json.dump(token_data, f, indent=2)
            print("\nArchivo 'ms_refresh_token_NUEVO.json' guardado en esta carpeta.")
            print("Copia su contenido y envialo al Bot.")
        else:
            print("\nNo se capturo el token. El navegador se cerro antes.")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
