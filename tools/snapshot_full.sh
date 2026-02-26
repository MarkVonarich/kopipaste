#!/usr/bin/env bash
set -euo pipefail
BASE=/root/bot_finuchet
DST="$BASE/_backups/full_dumps"
TS=$(date +%Y%m%d-%H%M%S)
WORK="$BASE/_tmp_snapshot_$TS"
OUT="$DST/finuchet-snapshot-$TS.tgz"
INCLUDE_ENV=${INCLUDE_ENV:-0} # поставь 1, если хочешь включать .env

mkdir -p "$DST" "$WORK"

# 1. код (без .git и __pycache__)
tar --exclude='.git' --exclude='__pycache__' -czf "$WORK/code.tgz" -C /root bot_finuchet

# 2. окружение
python3 -V > "$WORK/python_version.txt" 2>&1 || true
( pip3 freeze || pip freeze ) > "$WORK/requirements-freeze.txt" 2>&1 || true

# 3. systemd-юниты
cp -a /etc/systemd/system/finuchet.service "$WORK/" 2>/dev/null || true
cp -a /etc/systemd/system/finuchet.service.d "$WORK/" 2>/dev/null || true

# 4. .env (по флажку)
if [[ "$INCLUDE_ENV" == "1" && -f "$BASE/.env" ]]; then
  cp -a "$BASE/.env" "$WORK/.env"
fi

# 5. БД: схема + размеры + строки
sudo -u postgres pg_dump -s -d finance_bot > "$WORK/db_schema.sql"
sudo -u postgres psql -d finance_bot -c "\dt+ public.*" > "$WORK/db_tables_sizes.txt"
sudo -u postgres psql -d finance_bot -c "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC;" > "$WORK/db_rowcounts.txt"

# 6. хвост логов
journalctl -u finuchet -n 300 --no-pager > "$WORK/finuchet_journal_tail.txt" || true

# 7. итоговый архив
tar -C "$WORK" -czf "$OUT" .
rm -rf "$WORK"
echo "Done: $OUT"
