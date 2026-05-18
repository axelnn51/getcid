# 🌟 Guía: Crear Cuenta Profesional Gratuita (Microsoft Entra) para GetCID

**Objetivo:** Evitar los CAPTCHAs de Microsoft y el límite de 24 horas usando el comando `/deviceauth` del bot, el cual requiere una cuenta "Profesional o Educativa" para darte un token de **90 días**.

Dado que el programa de desarrolladores de Microsoft 365 ahora tiene restricciones, aquí tienes el método oficial y 100% gratuito directamente desde el portal de seguridad de Microsoft, **sin tarjeta de crédito**.

> [!TIP]
> **ATAJO: Tienes licencias Office 365 A3 (Pro Plus)**
> Las licencias A3 son Educativas, lo que significa que **ya son cuentas Profesionales/Educativas**. 
> Si ya tienes una cuenta con esta licencia, **puedes saltarte los pasos 1, 2 y 3** y pasar directamente a la sección "Cómo usarlo en tu Bot (GetCID)".

---

### 🛠️ Pasos a seguir (Toma 2 minutos):

#### 1. Crear el Espacio de Trabajo (Tenant)
1. Ve al panel oficial de Microsoft Entra: [https://entra.microsoft.com/](https://entra.microsoft.com/)
2. Inicia sesión con tu cuenta personal (ej. `axelnn51@gmail.com`).
3. En el menú izquierdo, ve a **Identidad (Identity)** > **Información general (Overview)**.
4. En la barra superior, haz clic en **Administrar inquilinos (Manage tenants)**.
5. Haz clic en el botón azul **+ Crear (+ Create)**.
6. Selecciona **Microsoft Entra ID** y dale a Siguiente.
7. Llena los datos básicos:
   - **Nombre de la organización:** `CDKeys Peru Bot`
   - **Nombre de dominio inicial:** Inventa uno único, por ejemplo: `cdkeysperubot1` (este será tu dominio temporal).
   - **País o región:** Perú.
8. Haz clic en **Revisar y crear**. Es posible que debas pasar un CAPTCHA rápido. Espera a que termine la creación (tarda ~1 minuto).

#### 2. Entrar a tu nuevo espacio
9. Cuando termine de crearse, verás un texto verde con un enlace a tu nuevo Inquilino. Haz clic ahí para cambiar a ese espacio de trabajo (o usa la opción "Cambiar" en Administrar Inquilinos).

#### 3. Crear el Usuario "Admin"
10. Una vez dentro de tu nuevo espacio "CDKeys Peru Bot", ve al menú izquierdo a **Usuarios (Users)** > **Todos los usuarios**.
11. Haz clic en **+ Nuevo usuario** > **Crear nuevo usuario**.
12. Llena los datos del usuario:
    - **Nombre principal del usuario:** Escribe `admin` (el correo final quedará como `admin@cdkeysperubot1.onmicrosoft.com`).
    - **Nombre de visualización:** `Admin CDKeys`
    - **Contraseña:** Asígnale una contraseña manual que recuerdes bien.
13. Baja y haz clic en **Crear**.

---

### 🚀 Cómo usarlo en tu Bot (GetCID)

1. Abre Telegram y ve a tu bot de GetCID.
2. Escribe el comando `/deviceauth`.
3. El bot te responderá con un **Código** (ej: `AS89YUL6R`) y un enlace (`https://login.microsoft.com/device`).
4. Abre ese enlace desde tu celular o PC.
5. Pega el código que te dio el bot.
6. **¡MUY IMPORTANTE!** Cuando te pida iniciar sesión, usa el correo que acabas de crear (ej: `admin@cdkeysperubot1.onmicrosoft.com`) y su contraseña.
7. Acepta los permisos que te pida.

¡Listo! Tu servidor obtendrá automáticamente un token nativo válido por **90 días**. El servidor se auto-renovará silenciosamente y **no volverás a sufrir bloqueos de CAPTCHA** generados por Playwright en tu VPS.
