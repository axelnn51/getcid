"""
Script de prueba visual para validar el CAPTCHA solver con CapMonster.
Abre una ventana visible de Chrome y fuerza un login en Microsoft para
provocar el FunCaptcha de Arkose Labs y resolverlo automáticamente.

IMPORTANTE: El interceptor de blob se configura ANTES del login para
atrapar el blob del tráfico de red cuando Arkose hace POST a fc/gt2.

Ejecución:
  cd getcid_service
  python test_captcha.py
"""
import asyncio
import os
import sys
import logging
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from dotenv import load_dotenv

# Cargar configuración (.env)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TestCaptcha")

from captcha_solver import (
    solve_captcha_on_page,
    setup_blob_interceptor,
    CAPMONSTER_USER_AGENT
)

async def run_test():
    # Obtener credenciales del .env para la prueba
    ms_accounts = os.getenv("MS_ACCOUNTS", "")
    email = os.getenv("MS_EMAIL", "")
    password = os.getenv("MS_PASSWORD", "")
    
    if ms_accounts and ":" in ms_accounts:
        first_acc = ms_accounts.split(",")[0]
        email, password = first_acc.split(":", 1)
    
    if not email or not password:
        logger.error("❌ ERROR: No hay cuentas configuradas en tu .env (MS_EMAIL o MS_ACCOUNTS).")
        return

    logger.info("🚀 Iniciando prueba visual de CAPTCHA Solver...")
    logger.info(f"🔑 Cuenta de prueba: {email}")
    logger.info(f"🌐 User-Agent: {CAPMONSTER_USER_AGENT}")

    async with async_playwright() as p:
        # Abrimos Chrome VISIBLE para ver el proceso
        browser = await p.chromium.launch(
            headless=False,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox'
            ]
        )
        
        # CRÍTICO: Usar el MISMO User-Agent que enviamos a CapMonster
        context = await browser.new_context(
            user_agent=CAPMONSTER_USER_AGENT
        )
        page = await context.new_page()
        
        # Aplicar evasión anti-bot
        stealth = Stealth()
        await stealth.apply_stealth_async(page)
        
        # ═══════════════════════════════════════════════════════════
        # PASO CLAVE: Configurar interceptor de blob ANTES del login
        # Así atrapamos el blob cuando Arkose hace POST a fc/gt2
        # ═══════════════════════════════════════════════════════════
        intercepted_data = await setup_blob_interceptor(page)
        
        # 1. Ir a la página de inicio de sesión de Microsoft
        logger.info("🌐 Navegando a Microsoft Login...")
        await page.goto("https://login.live.com/", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        
        # 2. Escribir el correo electrónico
        logger.info("✍️ Escribiendo correo electrónico...")
        try:
            await page.locator("input[type='email']").fill(email)
            await page.wait_for_timeout(1000)
        except Exception as e:
            logger.error(f"No se pudo escribir el email: {e}")
            await browser.close()
            return
        
        # Click en Siguiente
        clicked = False
        for selector in ["#idSIButton9", "input[type='submit']", "input[value='Next']", "input[value='Siguiente']"]:
            try:
                btn = page.locator(selector)
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    clicked = True
                    logger.info(f"✅ Click en Siguiente: {selector}")
                    break
            except:
                continue
        if not clicked:
            logger.warning("No se detectó botón Siguiente. Presionando Enter...")
            await page.locator("input[type='email']").press("Enter")
            
        await page.wait_for_timeout(3000)
        
        # 3. Manejar pantalla de "Elegir método de autenticación"
        #    Microsoft puede mostrar opciones como "Use password", "Use authenticator", etc.
        logger.info("🔍 Buscando opciones de autenticación...")
        use_password_clicked = False
        
        # Intentar varios selectores para "Use password" / "Usar contraseña"
        password_selectors = [
            "div[data-value='password']",           # Selector por data attribute
            "#FormsAuthentication",                   # ID común del link de password
            "#idA_PWD_SwitchToPassword",              # Otro ID de Microsoft
        ]
        for selector in password_selectors:
            try:
                btn = page.locator(selector)
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    use_password_clicked = True
                    logger.info(f"🔑 Click en 'Use password' exitoso: {selector}")
                    await page.wait_for_timeout(2000)
                    break
            except:
                continue
        
        # También buscar por texto visible
        if not use_password_clicked:
            for text in ["Password", "password", "Contraseña", "Use a password", "Use password"]:
                try:
                    link = page.get_by_text(text, exact=False).first
                    if await link.is_visible(timeout=1000):
                        await link.click()
                        use_password_clicked = True
                        logger.info(f"🔑 Click en opción '{text}' exitoso.")
                        await page.wait_for_timeout(2000)
                        break
                except:
                    continue
        
        if not use_password_clicked:
            logger.info("   No se encontró pantalla de selección de método. Continuando...")
        
        # 4. Escribir contraseña
        try:
            if await page.locator("input[type='password']").is_visible(timeout=5000):
                logger.info("✍️ Escribiendo contraseña...")
                await page.locator("input[type='password']").fill(password)
                await page.wait_for_timeout(1000)
                
                clicked_pw = False
                for selector in ["#idSIButton9", "input[type='submit']", "input[value='Sign in']", "input[value='Iniciar sesión']"]:
                    try:
                        btn = page.locator(selector)
                        if await btn.is_visible(timeout=1000):
                            await btn.click()
                            clicked_pw = True
                            logger.info(f"✅ Click en Iniciar Sesión: {selector}")
                            break
                    except:
                        continue
                if not clicked_pw:
                    await page.locator("input[type='password']").press("Enter")
                    
                await page.wait_for_timeout(4000)
                logger.info("✅ Login con contraseña completado.")
            else:
                logger.warning("⚠️ Campo de contraseña no apareció en 5s. Microsoft puede haber redirigido.")
        except Exception as e:
            logger.warning(f"Error en paso de contraseña: {e}")

        # 4. Chequear si estamos en una pantalla de CAPTCHA
        logger.info("🔍 Analizando pantalla en busca de CAPTCHA...")
        
        # Si no estamos en captcha/visualsupport, navegar ahí
        current_url = page.url.lower()
        if "captcha" not in current_url and "visualsupport" not in current_url:
            # Buscar "Stay signed in?" y hacer clic en "No"
            try:
                no_btn = page.locator("input[id='idBtn_Back']")
                if await no_btn.is_visible(timeout=2000):
                    await no_btn.click()
                    logger.info("🔘 Click en 'No' (Stay signed in)")
                    await page.wait_for_timeout(2000)
            except:
                pass
            
            logger.info("🌐 Navegando a VisualSupport para forzar sesión...")
            await page.goto("https://visualsupport.microsoft.com/", wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            
            # Buscar el botón "Get Started"
            try:
                btn = page.get_by_role("button", name="Get Started")
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    logger.info("🔘 Botón 'Get Started' pulsado.")
                    await page.wait_for_timeout(4000)
            except Exception as e:
                logger.warning(f"No se pudo pulsar 'Get Started': {e}")
        
        # 5. Ejecutar el solver (él se encarga de clic en Start + interceptar blob + enviar a CapMonster)
        logger.info("⚡ Ejecutando solucionador automático de CAPTCHA...")
        logger.info("   El solver hará: detectar CAPTCHA → clic en Start → interceptar blob → enviar a CapMonster")
        exito = await solve_captcha_on_page(
            page, 
            network_blob="",
            intercepted_data=intercepted_data
        )
        
        if exito:
            logger.info("🎉 ¡PRUEBA SUPERADA CON ÉXITO! El CAPTCHA fue resuelto y el token inyectado.")
            await page.wait_for_timeout(5000)
        else:
            logger.error("❌ La prueba no completó la resolución automática.")
            logger.info("💡 Posibles causas:")
            logger.info("   1. El blob no fue interceptado (CAPTCHA cargó antes del interceptor)")
            logger.info("   2. CapMonster no pudo resolver el puzzle (UNSOLVABLE)")
            logger.info("   3. El User-Agent no coincide con el requerido por CapMonster")
            
            # Guardar screenshot de diagnóstico
            try:
                await page.screenshot(path="debug_test_captcha_fail.png")
                logger.info("   📸 Screenshot guardado: debug_test_captcha_fail.png")
            except:
                pass
            
            await page.wait_for_timeout(5000)
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_test())
