#!/usr/bin/env bash
set -euo pipefail

TS=$(date +%Y%m%d-%H%M%S)
BASE=/root/bot_finuchet
SNAP_DIR="$BASE/_backups/snap-$TS"
mkdir -p "$SNAP_DIR"/{code,env,systemd,db,meta}

# 1) Подхватим окружение, чтобы увидеть DATABASE_URL
for f in /root/.env "$BASE/.env"; do [ -f "$f" ] && . "$f"; done
DBURL="${DATABASE_URL:-}"

# 2) Метаданные
( whoami; uname -a; date; python3 -V; psql --version || true ) > "$SNAP_DIR/meta/system.txt"
pip3 freeze > "$SNAP_DIR/meta/pip-freeze.txt" || true
systemctl --no-pager status finuchet > "$SNAP_DIR/meta/finuchet-status.txt" || true
systemctl show -p Requires,Wants,After finuchet.service > "$SNAP_DIR/systemd/links.txt" || true
systemctl --no-pager cat finuchet.service > "$SNAP_DIR/systemd/finuchet.service" || true
cp -a /etc/systemd/system/finuchet.service.d "$SNAP_DIR/systemd/" 2>/dev/null || true

# 3) Код (без _backups, __pycache__, .git)
rsync -a --delete \
  --exclude '_backups' --exclude '__pycache__' --exclude '.git' \
  "$BASE/" "$SNAP_DIR/code/"

# 4) ENV (немаскированные копии)
cp -a /root/.env           "$SNAP_DIR/env/root.env"  2>/dev/null || true
cp -a "$BASE/.env"         "$SNAP_DIR/env/app.env"   2>/dev/null || true
chmod 600 "$SNAP_DIR"/env/*.env 2>/dev/null || true

# 5) Дампы БД (если есть DATABASE_URL). Ошибки не валят снапшот.
if [ -n "$DBURL" ]; then
  pg_dump   -Fc -C -f "$SNAP_DIR/db/finance_bot.dump" "$DBURL"   || echo "[warn] pg_dump (custom) failed"
  pg_dump   -f        "$SNAP_DIR/db/finance_bot.sql"  "$DBURL"   || echo "[warn] pg_dump (plain) failed"
  pg_dumpall -g   >   "$SNAP_DIR/db/globals.sql"               || echo "[warn] pg_dumpall -g failed"
fi

# 6) Архив
TAR="$BASE/_backups/fc-snap-$TS.tgz"
tar -czf "$TAR" -C "$SNAP_DIR" .

# 7) (необязательно) ротация — держим последние 10 архивов
ls -1dt "$BASE"/_backups/fc-snap-*.tgz 2>/dev/null | tail -n +11 | xargs -r rm -f

echo "SNAP_DIR=$SNAP_DIR"
echo "ARCHIVE =$TAR"
