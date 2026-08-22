FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Instalar dependencias del sistema, Python, Xvfb, x11vnc, fluxbox (window manager ligero) y dependencias de Playwright
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    xvfb \
    x11vnc \
    fluxbox \
    wget \
    net-tools \
    novnc \
    websockify \
    curl \
    # Dependencias comunes de Playwright
    libnss3 \
    libatk-bridge2.0-0 \
    libx11-xcb1 \
    libxcb-dri3-0 \
    libdrm2 \
    libgbm1 \
    libasound2 \
    libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

# Crear directorio de trabajo
WORKDIR /app

# Copiar requirements primero para aprovechar el caché de Docker
COPY requirements.txt .

# Crear entorno virtual e instalar dependencias de Python
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir -r requirements.txt

# Instalar navegadores de Playwright (solo chromium para ahorrar espacio)
RUN playwright install chromium
RUN playwright install-deps chromium

# Copiar el resto del código
COPY . .

# Exponer puertos: 5000 para API, 6080 para noVNC web
EXPOSE 5000 6080

# Script de arranque
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
