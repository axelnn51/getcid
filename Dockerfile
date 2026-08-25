FROM python:3.11-slim

# Habilitar 32-bits (necesario para pidgenx.dll) e instalar Wine y MinGW
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends wine wine32 gcc-mingw-w64-i686 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar e instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fuente
COPY batch_cid.py main.py ./
COPY bin/ /app/bin/

# Compilar el motor C a un ejecutable Windows
RUN i686-w64-mingw32-gcc /app/bin/pidchecker.c -o /app/bin/pidchecker.exe -shared-libgcc

# Configurar Wine para que no tire popups gráficos
ENV WINEDEBUG=-all
ENV WINEPREFIX=/app/.wine
ENV WINEARCH=win32

# Iniciar un entorno wine vacío en el build para acelerar la primera ejecución
RUN winecfg || true

# Crear directorio de logs
RUN mkdir -p /app/logs

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
