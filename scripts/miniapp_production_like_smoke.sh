#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN="${1:-}"

cd "$ROOT/frontend"
npm run build

cd "$ROOT"
if [[ "$DRY_RUN" == "--dry-run" ]]; then
  .venv/bin/python - <<'PY'
import miniapp.http
print("miniapp production-like smoke dry-run ok")
PY
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required for non-dry-run smoke" >&2
  exit 1
fi

PORT="${MINIAPP_API_PORT:-8080}"
.venv/bin/gunicorn -c deploy/miniapp.gunicorn.conf.py miniapp.http:application --bind "127.0.0.1:${PORT}" &
PID="$!"
trap 'kill "$PID" >/dev/null 2>&1 || true' EXIT

for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:${PORT}/miniapp/health" >/dev/null; then
    echo "miniapp production-like smoke ok"
    exit 0
  fi
  sleep 0.5
done

echo "miniapp health did not become ready" >&2
exit 1
