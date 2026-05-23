#!/usr/bin/env bash
set -euo pipefail
TS=$(date +%Y%m%d-%H%M%S)
BASE=/root/bot_finuchet
OUT=$BASE/_backups/snap-$TS
mkdir -p "$OUT"/{code,env,systemd,db,meta}

# подхватываем .env, чтобы видеть DATABASE_URL
for f in /root/.env "$BASE/.env"; do [ -f "$f" ] && . "$f"; done

# метаданные
( whoami; uname -a; date; python3 -V; psql --version || true ) > "$OUT/meta/system.txt"
pip3 freeze > "$OUT/meta/pip-freeze.txt" || true
systemctl --no-pager status finuchet > "$OUT/meta/finuchet-status.txt" || true
systemctl show -p Requires,Wants,After finuchet.service > "$OUT/systemd/links.txt" || true
systemctl --no-pager cat finuchet.service > "$OUT/systemd/finuchet.service" || true
cp -a /etc/systemd/system/finuchet.service.d "$OUT/systemd/" 2>/dev/null || true

# код
rsync -a --delete --exclude '_backups' --exclude '__pycache__' --exclude '.git' "$BASE/" "$OUT/code/"

# окружение
cp -a /root/.env "$OUT/env/root.env" 2>/dev/null || true
cp -a "$BASE/.env" "$OUT/env/app.env" 2>/dev/null || true
chmod 600 "$OUT"/env/*.env 2>/dev/null || true

# дампы БД (если DATABASE_URL задан)
DBURL=${DATABASE_URL:-}
if [ -n "$DBURL" ]; then
  pg_dump -Fc -C -f "$OUT/db/finance_bot.dump" "$DBURL"
  pg_dump -f "$OUT/db/finance_bot.sql" "$DBURL"
  pg_dumpall -g > "$OUT/db/globals.sql"
fi

# архив
TAR=$BASE/_backups/fc-snap-$TS.tgz
tar -czf "$TAR" -C "$OUT" .




echo "$TAR"
