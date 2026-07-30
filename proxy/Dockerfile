FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    COVER_CACHE_DIR=/data/covers \
    PORT=8000

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Unprivileged runtime user, owning only the cover cache volume.
RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 shelf \
    && mkdir -p /data/covers \
    && chown -R shelf:shelf /data

USER shelf

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
