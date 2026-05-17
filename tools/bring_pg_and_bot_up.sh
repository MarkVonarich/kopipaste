#!/usr/bin/env bash
set -euo pipefail

echo "==> Load env"
[ -f /root/.env ] && set -a && . /root/.env && set +a || true
DBURL="${DATABASE_URL:-}"

echo "==> Ensure /run/postgresql exists"
install -d -o postgres -g postgres -m 2775 /run/postgresql

echo "==> Start/enable Postgres 14-main"
systemctl start postgresql@14-main.service || true
systemctl enable postgresql@14-main.service >/dev/null 2>&1 || true
sleep 1

echo "==> Check pg status"
systemctl --no-pager status postgresql@14-main.service | sed -n '1,20p'

echo "==> pg_isready"
command -v pg_isready >/dev/null 2>&1 && pg_isready -h 127.0.0.1 -p 5432 || true

echo "==> Tail PG log (last 50 lines)"
LOG=/var/log/postgresql/postgresql-14-main.log
[ -f "$LOG" ] && tail -n 50 "$LOG" || echo "(no PG log at $LOG)"

if [ -n "$DBURL" ]; then
  echo "==> Test psql via DATABASE_URL"
  psql "$DBURL" -c "select now();" -t -A
else
  echo "!! DATABASE_URL is empty in env. Fix /root/.env first."
  exit 1
fi

echo "==> Restart finuchet"
systemctl restart finuchet
sleep 1
systemctl --no-pager status finuchet | sed -n '1,25p'
echo "==> Last 50 lines of finuchet journal"
journalctl -u finuchet -n 50 --no-pager
