FROM python:3.12.5-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system app \
    && adduser --system --ingroup app app

WORKDIR /app

COPY pyproject.toml requirements.txt requirements-dev.txt README.md alembic.ini ./
COPY app ./app
COPY migrations ./migrations

RUN pip install --upgrade pip==24.2 \
    && pip install -r requirements.txt

RUN mkdir -p /var/cache/procurement-documents \
    && chown -R app:app /app /var/cache/procurement-documents

USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).read()"

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.api.main:app --host 0.0.0.0 --port 8080 & python -m app.scheduler.worker"]
