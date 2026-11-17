# CAFirm — production image (Render free tier / any Docker host)
# Python 3.14 slim; Flask 3.1 app served by gunicorn; PostgreSQL 18 target.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libpq5 backs psycopg's ctypes mode if the binary wheel is unavailable;
# curl is used by the optional image healthcheck below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl \
 && rm -rf /var/lib/apt/lists/*

# requirements.txt stays machine-neutral; the Linux image adds the psycopg
# binary extra (bundled libpq) and gunicorn as the production WSGI server.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt "psycopg[binary]>=3.2.0" gunicorn

COPY . .

RUN chmod +x deploy/start.sh && useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD curl -fsS http://127.0.0.1:${PORT:-5000}/health || exit 1

CMD ["bash", "deploy/start.sh"]
