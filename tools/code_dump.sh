#!/usr/bin/env bash
set -euo pipefail
TS=$(date +%Y%m%d-%H%M%S)
BASE=/root/bot_finuchet
OUT="$BASE/_backups/code_dump-$TS.txt"
mkdir -p "$BASE/_backups"

# подхват env, чтобы получить DATABASE_URL без маски
for f in /root/.env "$BASE/.env"; do [ -f "$f" ] && . "$f"; done
DBURL=${DATABASE_URL:-}

{
  echo "# code_dump @ $(date)  host=$(hostname)"
  echo

  echo "===== SYSTEM ====="
  whoami
  uname -a
  python3 -V 2>&1 || true
  psql --version 2>&1 || true
  echo

  echo "===== ENV FILES (UNMASKED) ====="
  echo "--- /root/.env ---";  [ -f /root/.env ] && cat /root/.env || echo "(missing)"
  echo "--- $BASE/.env ---"; [ -f $BASE/.env ] && cat $BASE/.env || echo "(missing)"
  echo

  echo "===== SYSTEMD UNIT ====="
  systemctl show -p Requires,Wants,After finuchet.service 2>/dev/null || true
  echo
  systemctl --no-pager cat finuchet.service 2>/dev/null || true
  echo
  [ -d /etc/systemd/system/finuchet.service.d ] && \
    (echo "----- drop-ins -----"; ls -la /etc/systemd/system/finuchet.service.d; \
     for f in /etc/systemd/system/finuchet.service.d/*; do echo "--- $f ---"; cat "$f"; echo; done) || true
  echo

  echo "===== PIP FREEZE ====="
  pip3 freeze 2>/dev/null || true
  echo

  echo "===== TREE ====="
  (cd "$BASE" && find . -maxdepth 4 -type f ! -path "./_backups/*" | sort)
  echo

  echo "===== FILES CONTENT ====="
  (cd "$BASE" && \
    find . -type f \
      \( -name "*.py" -o -name "*.sh" -o -name "*.txt" -o -name "*.md" -o -name "*.json" -o -name "*.yml" -o -name "*.yaml" -o -name "*.sql" \) \
      ! -path "./_backups/*" | sort | while read -r f; do
        echo "### BEGIN $f"; sed -n '1,200000p' "$f"; echo "### END $f"; echo;
      done)
  echo

  if [ -n "$DBURL" ]; then
    echo "===== DATABASE: URL ====="
    echo "$DBURL"
    echo

    echo "===== DATABASE: SCHEMA (pg_dump -s) ====="
    pg_dump -s "$DBURL" || echo "(pg_dump -s failed)"
    echo

    echo "===== DATABASE: PUBLIC TABLES & APPROX ROWS ====="
    psql "$DBURL" -At -F $'\t' -c "select c.relname, c.reltuples::bigint from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='r' order by 1;" || true
    echo

    echo "===== DATABASE: \dt LIKE LIST ====="
    psql "$DBURL" -P pager=off -c "\dt+ public.*" || true
    echo
  fi
} > "$OUT"

echo "$OUT"
