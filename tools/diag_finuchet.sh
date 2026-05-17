#!/usr/bin/env bash
set -uo pipefail

OUT="/root/bot_finuchet/_reports/diag-$(date +%Y%m%d-%H%M%S).txt"
mkdir -p /root/bot_finuchet/_reports

sec(){ echo; echo "===== $* ====="; }

{
  echo "# finuchet diag @ $(date)"
  echo "whoami: $(whoami)"
  echo "uname:  $(uname -a)"
  echo "pwd:    $(pwd)"

  sec "SYSTEMD UNIT (requires/wants/after + drop-ins)"
  systemctl show -p Requires,Wants,After finuchet.service
  systemctl --no-pager cat finuchet.service

  sec "MAIN PID / CMDLINE / CWD / ENV (masked)"
  PID=$(systemctl show -p MainPID finuchet.service | cut -d= -f2)
  echo "MainPID=$PID"
  if [ -n "$PID" ] && [ "$PID" -gt 0 ] && [ -e "/proc/$PID" ]; then
    ps -o pid,ppid,cmd -p "$PID"
    ls -l "/proc/$PID/cwd" || true
    echo "-- selected ENV (presence only) --"
    tr '\0' '\n' <"/proc/$PID/environ" | sed -n \
      -e 's/^TELEGRAM_TOKEN=.*/TELEGRAM_TOKEN=*SET*/p' \
      -e 's/^DATABASE_URL=.*/DATABASE_URL=*SET*/p' \
      -e 's/^CURRENCYBEACON_API_KEY=.*/CURRENCYBEACON_API_KEY=*SET*/p' \
      -e 's/^PYTHONPATH=.*/PYTHONPATH=&/p'
  else
    echo "Process not running or /proc missing"
  fi

  sec "FILES: main.py / routers/messages.py (sha256 + wc -l)"
  for f in /root/bot_finuchet/main.py /root/bot_finuchet/routers/messages.py; do
    [ -f "$f" ] && { sha256sum "$f"; wc -l "$f"; head -n 40 "$f"; echo "..."; tail -n 40 "$f"; } || echo "missing: $f"
    echo
  done

  sec "PYTHON IMPORT CHECK (без запуска polling)"
  PYTHONPATH=/root/bot_finuchet python3 - <<'PY'
import sys, traceback
print("sys.version:", sys.version)
print("sys.path[0:3]:", sys.path[0:3])
mods = ["settings", "routers.messages", "services.records", "utils.parsing", "ui.keyboards"]
for m in mods:
    try:
        __import__(m)
        print("[OK] import", m)
    except Exception as e:
        print("[FAIL] import", m, "->", repr(e))
        traceback.print_exc()
PY

  sec "ENV FROM FILES (.env masked)"
  for e in /root/.env /root/bot_finuchet/.env; do
    if [ -f "$e" ]; then
      echo "-- $e (checksum8=$(sha256sum "$e" | cut -c1-8)) --"
      sed -E 's/^([A-Z0-9_]+)=.*/\1=***MASKED***/' "$e" | sed -n '1,60p'
    else
      echo "missing: $e"
    fi
  done

  sec "POSTGRES CLUSTERS"
  pg_lsclusters 2>/dev/null || echo "(pg_lsclusters not available)"
  systemctl --no-pager status postgresql.service || true
  systemctl --no-pager status postgresql@14-main.service || true

  sec "POSTGRES 14 LOG (tail 120)"
  [ -f /var/log/postgresql/postgresql-14-main.log ] && tail -n 120 /var/log/postgresql/postgresql-14-main.log || echo "(no 14-main log)"

  sec "BOT JOURNAL (tail 200)"
  journalctl -u finuchet -n 200 --no-pager || true

} > "$OUT" 2>&1

echo "Wrote: $OUT"
