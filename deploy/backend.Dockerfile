FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libstdc++6 && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 appuser
WORKDIR /app/backend-core
COPY backend-core/pyproject.toml backend-core/uv.lock ./
RUN uv sync --frozen --no-dev
COPY backend-core ./
RUN mkdir -p /data /models && chown -R appuser:appuser /app /data /models
USER appuser
ENV WHISPER_GUIDE_DATA_DIR=/data WHISPER_GUIDE_SPEECH_MODEL_DIR=/models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20
EXPOSE 8000
CMD ["/app/backend-core/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
