FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Instalar Xvfb para pantalla virtual (evita detección de headless por Microsoft)
RUN apt-get update && apt-get install -y xvfb && rm -rf /var/lib/apt/lists/*

# Copiar requirements e instalar
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código (incluye states/ con sesión pre-guardada)
COPY . .

# Asegurar que el directorio de sesiones existe dentro de la imagen
RUN mkdir -p /app/states

# Exponer el puerto de FastAPI
EXPOSE 8000

# Usar xvfb-run para crear pantalla virtual → Chrome corre en modo "normal" (no headless)
# Microsoft ve un navegador real y no lanza CAPTCHA
CMD ["xvfb-run", "--auto-servernum", "--server-args=-screen 0 1280x720x24", "python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
