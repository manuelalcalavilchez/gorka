FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GEODATOS_DIR=/data/geodatos \
    ESTUDIOS_DIR=/data/estudios \
    MPLCONFIGDIR=/tmp/mpl

# libexpat1: la necesita GDAL (rasterio/pyogrio) y no viene en la imagen slim
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY config ./config
COPY prefact ./prefact
COPY scripts ./scripts
RUN python scripts/descargar_vendor.py

RUN useradd --create-home --uid 1000 app && mkdir -p /data/estudios && chown -R app /data /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/salud',timeout=4)" || exit 1

# Puerto fijo 8000 (Easypanel inyecta PORT=80, que un usuario sin root no puede abrir).
# Un solo proceso: los cálculos corren en hilos y el estado vive en ESTUDIOS_DIR.
CMD ["uvicorn", "prefact.web.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
