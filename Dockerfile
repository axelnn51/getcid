FROM python:3.11-slim

WORKDIR /app

# Copiar e instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fuente
COPY batch_cid.py main.py ./

# Crear directorio de logs
RUN mkdir -p /app/logs

# Exponer puerto
EXPOSE 8000

# Ejecutar
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
