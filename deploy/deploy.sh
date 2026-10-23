#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/docker-compose.yml" ]]; then
  ROOT_DIR="$SCRIPT_DIR"
else
  ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
fi
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/docker-compose.yml}"
IMAGE_ARCHIVE="${1:-$ROOT_DIR/whisper-guide-image.tar.gz}"

cd "$ROOT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required but was not found in PATH." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is unavailable. Start Docker Engine/Desktop and retry." >&2
  exit 1
fi
if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "Compose file not found: $COMPOSE_FILE" >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example; review it before exposing the service."
fi

mkdir -p data/sqlite data/backups
if [[ "$(id -u)" -eq 0 ]]; then
  chown -R 10001:10001 data/sqlite data/backups
elif command -v sudo >/dev/null 2>&1; then
  sudo chown -R 10001:10001 data/sqlite data/backups
else
  echo "data/sqlite and data/backups must be writable by container UID 10001." >&2
  echo "Run: sudo chown -R 10001:10001 data/sqlite data/backups" >&2
  exit 1
fi

if [[ ! -f "$IMAGE_ARCHIVE" ]]; then
  echo "Offline image archive not found: $IMAGE_ARCHIVE" >&2
  exit 1
fi

echo "Loading offline images from $IMAGE_ARCHIVE..."
gzip -dc "$IMAGE_ARCHIVE" | docker load
docker compose -f "$COMPOSE_FILE" up -d --no-build
docker compose -f "$COMPOSE_FILE" ps

echo "Checking backend health..."
docker compose -f "$COMPOSE_FILE" exec -T backend \
  /app/backend-core/.venv/bin/python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5).read().decode())"
echo "Whisper Guide is available at http://localhost:${WHISPER_GUIDE_HTTP_PORT:-8080}"
