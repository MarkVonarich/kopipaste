#!/usr/bin/env bash
set -euo pipefail
BASE="/root/bot_finuchet"
OUT="$BASE/_backups"
ts=$(date +%Y%m%d-%H%M%S)
file="$OUT/code_db_dump-$ts.txt"
mkdir -p "$OUT"

{
  echo "### CODE & SERVICE ### $ts"
  python3 -V; echo; pip3 --version || true; echo

  echo "# systemd unit:"
  systemctl cat finuchet; echo

  echo "# systemd show (key fields):"
  systemctl show -p User -p WorkingDirectory -p ExecStart -p Environment -p EnvironmentFiles finuchet.service
  echo

  echo "# .env (UNMASKED, BE CAREFUL):"
  if [ -f "$BASE/.env" ]; then
    cat "$BASE/.env"
  else
    echo "(no .env found)"
  fi
  echo

  echo "# code tree (mtime,size):"
  (cd "$BASE" && find . -maxdepth 3 -type f \( -name '*.py' -o -name 'requirements.txt' \) -printf "%p\t%TY-%Tm-%Td %TH:%TM\t%k KB\n")
  echo

  echo "# handlers & jobs (grep):"
  grep -RIn --include='*.py' "setMyCommands|CommandHandler|add_handler|JobQueue|job_queue|add_job|run_repeating|run_daily" "$BASE" || true
  echo

  echo "### DATABASE ###"
  sudo -u postgres psql -d finance_bot -X -q -v ON_ERROR_STOP=1 <<'SQL'
\pset pager off
\pset border 0
SELECT 'tables', count(*) FROM information_schema.tables WHERE table_schema='public';

SELECT table_name, reltuples::bigint AS approx_rows
FROM pg_class c
JOIN information_schema.tables t ON t.table_name=c.relname AND t.table_schema='public'
WHERE t.table_type='BASE TABLE'
ORDER BY table_name;

-- последние 10 записей (если есть)
SELECT id, user_id, record_date, type, category, amount, currency, comment, created_at
FROM records ORDER BY id DESC LIMIT 10;

-- алиасы и кэш валют
SELECT 'global_aliases', count(*) FROM global_aliases;
SELECT 'user_aliases',   count(*) FROM user_aliases;
SELECT 'fx_cache',       count(*) FROM fx_cache;

-- пользователи с напоминаниями
SELECT count(*) FILTER (WHERE reminder_hour IS NOT NULL) AS users_with_reminders FROM users;
SQL

  echo
  echo "### JOURNAL (tail 200) ###"
  journalctl -u finuchet -n 200 --no-pager
} > "$file"

echo "Dump saved: $file"
