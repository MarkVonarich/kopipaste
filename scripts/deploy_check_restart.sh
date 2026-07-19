#!/usr/bin/env bash
set -Eeuo pipefail

cd /root/bot_finuchet

PY="/root/bot_finuchet/.venv/bin/python"

echo "== Finuchet deploy check =="
git status --short --branch

if git diff --name-only --diff-filter=U | grep -q .; then
  echo "DEPLOYMENT FAILED — unresolved merge conflicts"
  exit 1
fi

git diff --check

"$PY" -m compileall main.py routers db services jobs utils ui ai
"$PY" -c "import main; import routers.commands; import routers.callbacks; import routers.messages; import db.database; import db.queries; import jobs.daily; import jobs.scheduler; import jobs.tokens_cleanup"

if [ -d tests ]; then
  "$PY" -m pytest -q
else
  echo "No tests directory found; skipping pytest."
fi

echo
echo "Pending migration files to review:"
ls -1 migrations/*.sql | tail -20

echo
read -r -p "Apply migrations listed in MIGRATIONS_TO_APPLY now? Type APPLY to continue: " apply_answer
if [ "$apply_answer" = "APPLY" ]; then
  : "${DATABASE_URL:?DATABASE_URL must be set}"
  : "${MIGRATIONS_TO_APPLY:?Set MIGRATIONS_TO_APPLY to a space-separated list of migration files}"
  for migration in $MIGRATIONS_TO_APPLY; do
    echo "Applying $migration"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$migration"
  done
else
  echo "Migrations not applied."
fi

echo
read -r -p "Restart finuchet service now? Type RESTART to continue: " restart_answer
if [ "$restart_answer" = "RESTART" ]; then
  sudo systemctl restart finuchet
  sudo systemctl status finuchet --no-pager -l
  sudo journalctl -u finuchet -n 120 --no-pager
  echo "DEPLOYMENT PASSED — READY TO TEST"
else
  echo "DEPLOYMENT FAILED — SERVICE NOT CHANGED/ROLLBACK REQUIRED"
  exit 1
fi
