#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./restore_from_backup.sh /path/to/db.dump /path/to/code.tgz
#
# ВАЖНО:
# - .env не трогаем
# - перед восстановлением останавливаем сервис

DB_DUMP="${1:-}"
CODE_TGZ="${2:-}"

if [[ -z "${DB_DUMP}" || -z "${CODE_TGZ}" ]]; then
  echo "Usage: $0 /path/to/db.dump /path/to/code.tgz"
  exit 1
fi

if [[ ! -f "${DB_DUMP}" ]]; then
  echo "DB dump not found: ${DB_DUMP}"
  exit 1
fi

if [[ ! -f "${CODE_TGZ}" ]]; then
  echo "Code tgz not found: ${CODE_TGZ}"
  exit 1
fi

BASE="/root/bot_finuchet"
TS="$(date +%Y%m%d-%H%M%S)"
CUR_BACKUP="/root/bot_finuchet._restore_backup_${TS}"

echo "==> Stop service finuchet"
systemctl stop finuchet || true

echo "==> Backup current code to ${CUR_BACKUP}"
rsync -a --exclude='.env' "${BASE}/" "${CUR_BACKUP}/"

echo "==> Restore code from ${CODE_TGZ} (excluding .env)"
TMP="/tmp/finuchet_restore_${TS}"
mkdir -p "${TMP}"
tar -xzf "${CODE_TGZ}" -C "${TMP}"

# CODE_TGZ у тебя создаётся как набор директорий (main.py/jobs/services/routers/...)
# поэтому накатываем поверх, но .env не трогаем
rsync -a --exclude='.env' "${TMP}/" "${BASE}/"

echo "==> Restore DB from ${DB_DUMP}"
cd /tmp
sudo -u postgres pg_restore --clean --if-exists -d finance_bot "${DB_DUMP}"

echo "==> Start service finuchet"
systemctl start finuchet

echo "DONE. If needed, previous code is here: ${CUR_BACKUP}"
