#!/usr/bin/env bash
set -euo pipefail

BASE="/root/bot_finuchet"
OUT="${BASE}/_backups/full_dumps"
TAG="${1:-pre_change}"
TS="$(date +%Y%m%d-%H%M%S)"

mkdir -p "${OUT}"

echo "DB dump -> ${OUT}/${TAG}_${TS}.dump"
cd /tmp
sudo -u postgres pg_dump -d finance_bot -Fc > "${OUT}/${TAG}_${TS}.dump"

echo "CODE tgz -> ${OUT}/${TAG}_code_${TS}.tgz"
cd "${BASE}"
tar -czf "${OUT}/${TAG}_code_${TS}.tgz" \
  main.py jobs services routers db utils ui logging_config.py settings.py 2>/dev/null || true

ls -lh "${OUT}" | tail -n 8
echo "DONE"
