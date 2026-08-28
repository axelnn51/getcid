FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema (Tesseract y OpenCV)
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Descargar el modelo "best" de Tesseract (fundamental para leer fotos borrosas)
RUN mkdir -p /usr/local/share/tessdata && \
    wget -q -O /usr/local/share/tessdata/eng.traineddata https://github.com/tesseract-ocr/tessdata_best/raw/main/eng.traineddata
ENV TESSDATA_PREFIX=/usr/local/share/tessdata

# Copiar e instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fuente
COPY *.py ./

# Crear directorio de logs
RUN mkdir -p /app/logs

# Exponer puerto
EXPOSE 8000

# Ejecutar
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
