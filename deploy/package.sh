#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE_DIR="$ROOT_DIR/release"
IMAGE_ARCHIVE="$RELEASE_DIR/whisper-guide-image.tar.gz"
RELEASE_ARCHIVE="$ROOT_DIR/whisper-guide-release.tar"
BACKEND_IMAGE="whisper-guide-backend:0.1.0"
FRONTEND_IMAGE="whisper-guide-frontend:0.1.0"

cd "$ROOT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required but was not found in PATH." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is unavailable. Start Docker Engine/Desktop and retry." >&2
  exit 1
fi

rm -rf "$RELEASE_DIR" "$RELEASE_ARCHIVE"
mkdir -p "$RELEASE_DIR"

docker compose -f docker-compose.yml build --progress=plain
docker image prune -f
docker save "$BACKEND_IMAGE" "$FRONTEND_IMAGE" | gzip -c > "$IMAGE_ARCHIVE"

cp docker-compose.yml .env.example deploy/deploy.sh DEPLOY.md "$RELEASE_DIR/"
mkdir -p "$RELEASE_DIR/scripts"
cp scripts/backup.py "$RELEASE_DIR/scripts/"

tar -cf "$RELEASE_ARCHIVE" -C "$ROOT_DIR" release
rm -f "$IMAGE_ARCHIVE"
echo "Release contents:"
find "$RELEASE_DIR" -maxdepth 2 -type f -printf '%P %s bytes\n' | sort
echo "Release archive: $RELEASE_ARCHIVE"
