FROM node:24-alpine AS frontend-build
WORKDIR /src
RUN corepack enable && corepack prepare pnpm@11.24.0 --activate
COPY apps/frontend/package.json apps/frontend/pnpm-lock.yaml apps/frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/frontend ./
RUN pnpm run build

FROM python:3.12-slim AS backend-deps
COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /uvx /bin/
# Keep only sources/native libraries; runtime disables bytecode writes too.
ENV UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1
WORKDIR /app/backend-core
COPY apps/backend-core/pyproject.toml apps/backend-core/uv.lock apps/backend-core/README.md ./
RUN uv sync --frozen --no-dev --no-cache

FROM python:3.12-slim AS backend-runtime
ARG APP_VERSION=dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.version=$APP_VERSION org.opencontainers.image.revision=$VCS_REF
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser
WORKDIR /app/backend-core
COPY --from=backend-deps /app/backend-core/.venv /app/backend-core/.venv
# INT8 encoder/joiner retain speed; FP32 decoder avoids repeated-token errors.
# Keep the model layer before application code so code changes do not invalidate it.
COPY --chown=appuser:appuser apps/backend-core/models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20/tokens.txt \
    apps/backend-core/models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20/encoder-epoch-99-avg-1.int8.onnx \
    apps/backend-core/models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20/decoder-epoch-99-avg-1.onnx \
    apps/backend-core/models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20/joiner-epoch-99-avg-1.int8.onnx \
    /models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20/
# Runtime needs the application and installed environment only. Download tools,
# lockfiles and tests stay outside the runtime image.
COPY apps/backend-core/app ./app
RUN mkdir -p /data /backups \
    && chown appuser:appuser /data /backups
USER appuser
ENV WHISPER_GUIDE_DATA_DIR=/data \
    WHISPER_GUIDE_BACKUP_DIR=/backups \
    WHISPER_GUIDE_SPEECH_MODEL_DIR=/models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20
# Catch ignored Python packages and incomplete bundled models before tagging an image.
RUN /app/backend-core/.venv/bin/python -c "from app.main import app; from app.config import get_settings; from app.infrastructure.speech import SpeechServices; assert SpeechServices(get_settings()).status()['stt'] == 'ready', 'Bundled STT model unavailable'"
EXPOSE 8000
CMD ["/app/backend-core/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

FROM nginx:1.29-alpine AS frontend-runtime
ARG APP_VERSION=dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.version=$APP_VERSION org.opencontainers.image.revision=$VCS_REF
COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /src/dist /usr/share/nginx/html
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
