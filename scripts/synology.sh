#!/bin/sh
set -eu
project_dir=$(cd "$(dirname "$0")/.." && pwd)
cd "$project_dir"
if ! docker compose version >/dev/null 2>&1; then
    echo 'Docker Compose v2 is required (Synology Container Manager).'
    exit 1
fi
if [ ! -f .env ]; then
    cp .env.example .env
    echo 'Created .env. Set ADMIN_PASSWORD, Toss credentials and STOCK_BIND before starting.'
    exit 1
fi
# docker compose 는 .env 를 자동으로 읽는다. --env-file 을 붙일 필요가 없고,
# 시놀로지 Container Manager 처럼 그 옵션을 줄 수 없는 환경에서도 그대로 동작한다.
compose() { docker compose "$@"; }
case "${1:-help}" in
    build) docker compose -f docker-compose.yml -f docker-compose.build.yml build --pull stock ;;
    start)
        compose pull stock
        compose up -d stock
        ;;
    login) compose exec -T stock codex -c 'cli_auth_credentials_store="file"' login --device-auth ;;
    status)
        compose ps
        compose exec -T stock codex -c 'cli_auth_credentials_store="file"' login status
        ;;
    stop) compose stop stock ;;
    backup)
        mkdir -p backups
        destination="backups/stock-$(date +%Y%m%d-%H%M%S).sqlite3"
        compose exec -T stock python -c 'import os,sqlite3; source=sqlite3.connect(os.environ["SQT_DB_PATH"]); target=sqlite3.connect("/data/export.sqlite3"); source.backup(target); target.close(); source.close()'
        compose cp stock:/data/export.sqlite3 "$destination"
        echo "Saved SQLite backup: $destination"
        ;;
    *) echo 'Usage: sh scripts/synology.sh build|start|login|status|stop|backup' ;;
esac
