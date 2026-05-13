FROM node:20-slim

# Instalar dependencias del sistema para sharp, better-sqlite3 y Puppeteer (chromium)
RUN apt-get update && apt-get install -y \
    python3 \
    make \
    g++ \
    chromium \
    && rm -rf /var/lib/apt/lists/*

ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

# Copiar package.json primero para cachear las dependencias
COPY package*.json ./
RUN npm ci --production

# Copiar el resto del código
COPY . .

# Crear directorios necesarios
RUN mkdir -p uploads data

EXPOSE 3000

CMD ["node", "index.js"]
