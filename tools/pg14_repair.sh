#!/usr/bin/env bash
set -euo pipefail
sec(){ echo; echo "===== $* ====="; }

DATA="/var/lib/postgresql/14/main"
PIDF="$DATA/postmaster.pid"
RUN="/run/postgresql"
LOG="/var/log/postgresql/postgresql-14-main.log"

sec "Disk usage"
df -h
df -i || true

sec "Ensure $RUN"
install -d -o postgres -g postgres -m 2775 "$RUN"

sec "pg_lsclusters"
pg_lsclusters || true

sec "Fix owners/permissions for data dir"
if [ -d "/var/lib/postgresql/14" ]; then chown -R postgres:postgres /var/lib/postgresql/14; fi
if [ -d "$DATA" ]; then chmod 700 "$DATA" || true; fi
ls -ld "$DATA" || true

sec "Remove stale postmaster.pid (if no postgres)"
if [ -f "$PIDF" ]; then
  if pgrep -u postgres -f "postgres.*14/main" >/dev/null; then
    echo "postgres running (pgrep found), not touching $PIDF"
  else
    echo "no postgres process, removing stale $PIDF"; rm -f "$PIDF"
  fi
else
  echo "no $PIDF (ok)"
fi

sec "Start service"
systemctl start postgresql@14-main.service || true
systemctl --no-pager status postgresql@14-main.service || true

sec "Tail postgres log"
[ -f "$LOG" ] && tail -n 200 "$LOG" || echo "(no 14-main log at $LOG)"
