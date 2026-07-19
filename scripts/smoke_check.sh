#!/usr/bin/env bash
set -Eeuo pipefail

cd /root/bot_finuchet

PY="/root/bot_finuchet/.venv/bin/python"

git status --short --branch
git diff --check
"$PY" -m compileall main.py routers db services jobs utils ui ai
"$PY" -c "import main; import routers.commands; import routers.callbacks; import routers.messages; import db.database; import db.queries; import jobs.daily; import jobs.scheduler; import jobs.tokens_cleanup"

if [ -d tests ]; then
  "$PY" -m pytest -q
fi

echo "SMOKE CHECK PASSED — run Telegram checklist in docs/SMOKE_TESTS.md"
