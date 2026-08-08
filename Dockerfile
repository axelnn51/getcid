FROM python:3.11-slim

WORKDIR /app

# Instalar solo dependencias del sistema necesarias
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements y instalar dependencias Python
# Excluimos playwright ya que no es necesario en el backend "Zero-Browser"
COPY requirements.txt .
RUN grep -v "playwright" requirements.txt > req_backend.txt && \
    pip install --no-cache-dir -r req_backend.txt

# Copiar el código fuente
COPY core.py auth_http.py main.py ./

# Exponer el puerto
EXPOSE 8000

# Comando para ejecutar la aplicación
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
