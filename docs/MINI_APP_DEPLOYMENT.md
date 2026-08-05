# Mini App Deployment

PR 1 introduced the Mini App foundation migrations. PR 2 completes the MVP UI/API surface and does not add a production migration.

## Migration

Production command, when approved:

```bash
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f migrations/20260804_016_miniapp_foundation.sql
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f migrations/20260804_017_miniapp_acceptance_hardening.sql
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f migrations/20260804_018_miniapp_transactional_idempotency.sql
```

Rollback for the new Mini App tables only:

```sql
DROP TABLE IF EXISTS public.miniapp_idempotency_keys;
DROP TABLE IF EXISTS public.miniapp_user_preferences;
DROP TABLE IF EXISTS public.miniapp_rate_limits;
```

The operation, idempotency lease and rate-limit cleanup indexes created by the migrations are additive and can be dropped separately if a database rollback plan requires it.

## Service Integration

The WSGI app is exposed as:

```text
miniapp.http:application
```

Production hosting should terminate TLS before Telegram opens the Mini App URL. Configure the frontend build output and WSGI route in infrastructure, not in application secrets.

Recommended architecture:

- build frontend with `npm ci && npm run build`;
- serve `frontend/dist` as static files over HTTPS;
- run API with pinned `gunicorn==23.0.0` and `miniapp.http:application`;
- proxy `/miniapp/api/*` and `/miniapp/health` to Gunicorn under the same trusted origin;
- keep strict CSP compatible with the Vite output;
- apply reverse-proxy rate limiting in addition to PostgreSQL-backed application rate limiting;
- write Gunicorn access/error logs under `/var/log/finuchet/`.

Safe templates:

- `deploy/miniapp.gunicorn.conf.py`
- `deploy/nginx-miniapp.example.conf`

## Environment

Required existing variables:

- `TELEGRAM_TOKEN`
- `DATABASE_URL`

Optional:

- `MINIAPP_VERSION`
- `MINIAPP_INITDATA_MAX_AGE_SECONDS`
- `MINIAPP_PUBLIC_URL`
- `MINIAPP_PRIVACY_URL`
- `MINIAPP_TERMS_URL`
- `MINIAPP_HELP_URL`

Do not modify PostHog, systemd unit files or production secrets as part of PR 1.
Do not modify PostHog, systemd unit files or production secrets as part of PR 2.

## Release Checks

- Backend compile succeeds with `miniapp`.
- Mini App imports succeed.
- Python tests pass.
- Frontend typecheck, lint, tests and build pass.
- Frontend build installs dependencies from `frontend/package-lock.json`, including the pinned chart library.
- Bot smoke check still passes.
- Telegram Mini App opens inside Telegram with signed `initData`.

## Local Production-Like Smoke

Dry run:

```bash
./scripts/miniapp_production_like_smoke.sh --dry-run
```

Full local smoke, after dependencies are installed:

```bash
./scripts/miniapp_production_like_smoke.sh
```

## Telegram Entry Point

The `/app` command shows a WebApp button only when `MINIAPP_PUBLIC_URL` is configured. If absent, the bot replies that Mini App is unavailable. BotFather menu-button configuration is a separate manual production step and is not performed by tests or deploy scripts.

Dry-run documentation command:

```bash
MINIAPP_PUBLIC_URL=https://app.example.com .venv/bin/python - <<'PY'
from settings import MINIAPP_PUBLIC_URL
print("Configure BotFather menu button manually:", MINIAPP_PUBLIC_URL)
PY
```
