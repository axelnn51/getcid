FROM node:20-slim

# Instalar dependencias del sistema para sharp y better-sqlite3
RUN apt-get update && apt-get install -y \
    python3 \
    make \
    g++ \
    && rm -rf /var/lib/apt/lists/*

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
