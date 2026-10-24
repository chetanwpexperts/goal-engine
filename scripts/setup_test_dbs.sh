#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

command -v docker >/dev/null 2>&1 || {
  echo "Docker is required"
  exit 1
}

docker compose version >/dev/null 2>&1 || {
  echo "Docker Compose v2 is required"
  exit 1
}

docker compose -f docker-compose.test.yml up -d --wait

docker compose -f docker-compose.test.yml exec -T mariadb \
  mariadb -ushopapp -p'app pass' shop_db \
  -e "SELECT VERSION(), DATABASE(), CURRENT_USER();"

docker compose -f docker-compose.test.yml exec -T postgres \
  psql -U shopapp -d shop_pg \
  -c "SELECT version(), current_database(), current_user;"

echo "TEST-DBS-OK"
