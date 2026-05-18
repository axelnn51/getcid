FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Instalar Xvfb para pantalla virtual (evita detección de headless por Microsoft)
RUN apt-get update && apt-get install -y xvfb dbus-x11 && rm -rf /var/lib/apt/lists/*

# Copiar requirements e instalar
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código (incluye states/ con sesión pre-guardada)
COPY . .

# Asegurar que el directorio de sesiones existe y el script de arranque es ejecutable
RUN mkdir -p /app/states && chmod +x /app/start.sh

# Exponer el puerto de FastAPI
EXPOSE 8000

# Usar script de arranque que inicia Xvfb + uvicorn
CMD ["/bin/bash", "/app/start.sh"]
