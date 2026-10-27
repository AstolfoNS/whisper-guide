#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
for file in VERSION release.json SHA256SUMS docker-compose.yml .env.example whisper-guide-image.tar.gz DEPLOY.md scripts/backup.py deploy.sh verify_release.py; do
  if [[ ! -f "$file" || -L "$file" ]]; then
    echo "Missing or unsafe release file: $file. Use a complete versioned release bundle." >&2
    exit 1
  fi
done
if [[ -L scripts ]]; then echo "Unsafe scripts directory." >&2; exit 1; fi
expected=(VERSION release.json docker-compose.yml .env.example whisper-guide-image.tar.gz DEPLOY.md scripts/backup.py deploy.sh verify_release.py)
declare -A seen=()
while IFS= read -r line; do
  if [[ ! "$line" =~ ^([0-9a-f]{64})\ \ (.+)$ ]]; then echo "Invalid checksum entry." >&2; exit 1; fi
  name="${BASH_REMATCH[2]}"
  allowed=false
  for item in "${expected[@]}"; do [[ "$name" != "$item" ]] || allowed=true; done
  if [[ "$allowed" != true || -n "${seen[$name]:-}" ]]; then echo "Unexpected checksum path." >&2; exit 1; fi
  seen[$name]=1
done < SHA256SUMS
if [[ "${#seen[@]}" -ne "${#expected[@]}" ]]; then echo "Incomplete checksum manifest." >&2; exit 1; fi
sha256sum --strict -c SHA256SUMS
VERSION="$(cat VERSION)"
if [[ ! "$VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then echo "Invalid VERSION." >&2; exit 1; fi
command -v docker >/dev/null
docker info >/dev/null
BACKEND_IMAGE="whisper-guide-backend:$VERSION"
FRONTEND_IMAGE="whisper-guide-frontend:$VERSION"
gzip -dc whisper-guide-image.tar.gz | docker load
{ printf '{"engine":'; docker info --format '{{json .}}'; printf ',"images":'; docker image inspect "$BACKEND_IMAGE" "$FRONTEND_IMAGE"; printf '}'; } |
  docker run --rm -i --pull never --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --mount "type=bind,src=$SCRIPT_DIR/VERSION,dst=/release/VERSION,readonly" \
    --mount "type=bind,src=$SCRIPT_DIR/release.json,dst=/release/release.json,readonly" \
    --mount "type=bind,src=$SCRIPT_DIR/docker-compose.yml,dst=/release/docker-compose.yml,readonly" \
    --mount "type=bind,src=$SCRIPT_DIR/verify_release.py,dst=/release/verify_release.py,readonly" \
    --entrypoint /app/backend-core/.venv/bin/python "$BACKEND_IMAGE" \
    /release/verify_release.py --release-dir /release
docker compose -f docker-compose.yml config --quiet
if [[ ! -f .env ]]; then cp .env.example .env; chmod 600 .env; fi
mkdir -p data/sqlite data/backups
if [[ "$(id -u)" -eq 0 ]]; then
  chown -R 10001:10001 data/sqlite data/backups
elif command -v sudo >/dev/null; then
  sudo chown -R 10001:10001 data/sqlite data/backups
else
  echo "Grant UID 10001 write access to data/sqlite and data/backups, then retry." >&2
  exit 1
fi
docker compose -f docker-compose.yml up -d --no-build --pull never --wait --wait-timeout 180
docker compose -f docker-compose.yml ps
