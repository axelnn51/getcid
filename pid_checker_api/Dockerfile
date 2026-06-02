FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema (git para clonar submodules)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Clonar winkeycheck con submodules (motor real de verificación)
RUN git clone --recursive https://github.com/WitherOrNot/winkeycheck.git /app/winkeycheck

# Copiar configuraciones adicionales (Office LTSC 2024, Win 11, etc.)
COPY ./pkeyconfigs /app/pkeyconfigs

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./app ./app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
