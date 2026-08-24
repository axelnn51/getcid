FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Instalar dependencias del sistema, Python, Xvfb, x11vnc, fluxbox (window manager ligero)
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
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Instalar Google Chrome (necesario para nodriver — se conecta directo vía CDP, sin chromedriver)
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list' \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Instalar Cloudflared para el túnel de acceso global
RUN curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb && \
    dpkg -i cloudflared.deb && \
    rm cloudflared.deb

# Crear directorio de trabajo
WORKDIR /app

# Copiar requirements primero para aprovechar el caché de Docker
COPY requirements.txt .

# Crear entorno virtual e instalar dependencias de Python
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código
COPY . .

# Exponer puertos: 5000 para API, 6080 para noVNC web
EXPOSE 5000 6080

# Script de arranque
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
