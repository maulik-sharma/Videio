# syntax=docker/dockerfile:1

# ---- builder: resolve dependencies from uv.lock into /app/.venv ----
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---- runtime base shared by every backend target ----
FROM python:3.12-slim-bookworm AS runtime
ARG VERSION=dev
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIDEIO_VERSION=${VERSION}

RUN groupadd --system --gid 10001 videio \
    && useradd --system --uid 10001 --gid videio --no-create-home videio

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
USER videio

# ---- api: FastAPI under uvicorn; also runs migrate (python -m app.db.migrate) ----
FROM runtime AS api
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status != 200)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]

# ---- worker: ffmpeg plus the same application package ----
FROM runtime AS worker
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
USER videio
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["ffmpeg", "-version"]
CMD ["taskiq", "worker", "app.worker.broker:broker", "app.worker.tasks", "--workers", "1", "--ack-type", "when_executed", "--wait-tasks-timeout", "300"]
