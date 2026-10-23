# syntax=docker/dockerfile:1.7

FROM node:24-alpine AS frontend-build
WORKDIR /src
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
COPY frontend/package.json frontend/package-lock.json ./
RUN pnpm install --no-frozen-lockfile
COPY frontend ./
RUN pnpm run build

FROM python:3.12-slim AS backend-deps
COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /uvx /bin/
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app/backend-core
COPY backend-core/pyproject.toml backend-core/uv.lock backend-core/README.md ./
RUN uv sync --frozen --no-dev --no-cache

FROM python:3.12-slim AS backend-runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser
WORKDIR /app/backend-core
COPY --from=backend-deps /app/backend-core/.venv /app/backend-core/.venv
# Keep the model layer before application code so code changes do not invalidate it.
COPY backend-core/models/ /models/
COPY backend-core/pyproject.toml backend-core/uv.lock ./
COPY backend-core/app ./app
COPY backend-core/main.py ./main.py
COPY backend-core/scripts ./scripts
RUN mkdir -p /data /backups \
    && chown -R appuser:appuser /app /data /backups /models
USER appuser
ENV WHISPER_GUIDE_DATA_DIR=/data \
    WHISPER_GUIDE_BACKUP_DIR=/backups \
    WHISPER_GUIDE_SPEECH_MODEL_DIR=/models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20 \
    WHISPER_GUIDE_SERVER_HOST=0.0.0.0 \
    WHISPER_GUIDE_SERVER_PORT=8000
EXPOSE 8000
CMD ["/app/backend-core/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

FROM nginx:1.29-alpine AS frontend-runtime
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /src/dist /usr/share/nginx/html
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
