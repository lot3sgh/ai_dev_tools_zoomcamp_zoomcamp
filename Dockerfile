# Health Takeout pipeline — the sync CLI, containerized for scheduled runs on the Linux host.
#
# Dependencies come from the pinned, hashed requirements.txt (generated with
# `uv export --no-dev --format requirements-txt --no-emit-package`). The package itself runs
# straight from /app/src via PYTHONPATH, so the image needs no build backend.
#
# Runtime inputs (not baked in): .env and the Service Account key, mounted by docker compose
# (see the `sync` service in docker-compose.yml). Entry point matches the README's
# `pipeline sync --source drive`.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 1. Dependencies — pinned, hashed; this layer only rebuilds when requirements.txt changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 2. Application source.
COPY src ./src

# 3. Run as an unprivileged user (no write access needed; /app/.env is read-only).
RUN useradd --create-home --uid 10001 pipeline \
    && chown -R pipeline:pipeline /app
USER pipeline

ENTRYPOINT ["python", "-m", "pipeline"]
CMD ["sync", "--source", "drive"]