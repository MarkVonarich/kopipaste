# Mini App Local Setup

## Backend Checks

From the repository root:

```bash
env TELEGRAM_TOKEN=test DATABASE_URL=postgresql://test:test@localhost/test \
  .venv/bin/python -m compileall main.py routers db services jobs ui utils ai scripts miniapp
```

```bash
env TELEGRAM_TOKEN=test DATABASE_URL=postgresql://test:test@localhost/test \
  .venv/bin/python - <<'PY'
import miniapp.auth
import miniapp.api
import miniapp.http
print("miniapp imports ok")
PY
```

## Frontend

```bash
cd frontend
npm ci
npm run typecheck
npm run lint
npm test
npm run build
```

PR 2 charts use the pinned `chart.js` package from `package-lock.json`. Do not use CDN scripts in local or production builds.

Production-like dry run:

```bash
cd /root/bot_finuchet
./scripts/miniapp_production_like_smoke.sh --dry-run
```

Start the local Vite server:

```bash
cd frontend
npm run dev
```

The frontend proxies `/miniapp/api` and `/miniapp/health` to `http://127.0.0.1:8080`.

## Local Auth Note

The Mini App requires signed Telegram WebApp `initData`. For manual browser testing, use a staging bot and open the Vite URL from Telegram, or inject a locally generated signed fixture only in a development harness. Do not disable signature verification in application code.

Create-form retry behavior: the frontend generates one idempotency key when the form opens and reuses it until success or cancellation. Keep the same browser session open when testing retry after a simulated timeout. A fast double submit should receive the original completed response or `idempotency_pending`; it must not create a second operation or duplicate post-commit hooks.

Goal contribution retry behavior is similar: the contribution sheet generates one idempotency key and reuses it until the movement is confirmed or the sheet is closed.
