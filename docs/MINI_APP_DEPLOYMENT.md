# Mini App Deployment

PR 1 introduced the Mini App foundation migrations. PR 2 completes the MVP UI/API surface. The smart Home/profile release adds a preferred-name column and does not apply it automatically in production.

## Migration

Production command, when approved:

```bash
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f migrations/20260804_016_miniapp_foundation.sql
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f migrations/20260804_017_miniapp_acceptance_hardening.sql
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f migrations/20260804_018_miniapp_transactional_idempotency.sql
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f migrations/20260806_019_user_preferred_name.sql
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f migrations/20260806_020_quiet_hours_enabled.sql
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
- keep strict CSP compatible with the Vite output and Telegram SDK;
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

Apply migration `20260806_020_quiet_hours_enabled.sql` before deploying the quiet-hours editor code. It backfills `quiet_hours_enabled` from existing start/end values and preserves saved times when users disable quiet hours.

## Release Checks

- Backend compile succeeds with `miniapp`.
- Mini App imports succeed.
- Python tests pass.
- Frontend typecheck, lint, tests and build pass.
- Frontend entrypoint loads `https://telegram.org/js/telegram-web-app.js?63` before the Vite module.
- CSP allows `script-src 'self' https://telegram.org` and does not use `unsafe-inline` or `unsafe-eval` for scripts.
- Production `dist/index.html` has the Telegram SDK, no inline scripts, no `nomodule` scripts and no Vite legacy loader.
- Frontend build installs dependencies from `frontend/package-lock.json`, including the pinned chart library.
- Bot smoke check still passes.
- Telegram Mini App opens inside Telegram with signed `initData`.
- Dotfile probes such as `/.env`, `/.git/HEAD`, `/config/.env`, `/app/.env`, `/api/.env`, `/application/.env` and `/functions/.env` return 404 instead of the SPA HTML.
- ACME paths under `/.well-known/acme-challenge/` remain available for certificate renewal.

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

The frontend includes the official Telegram WebApp SDK in `frontend/index.html`.
The SDK must load before the Vite module entrypoint so `window.Telegram.WebApp`,
`ready()`, `expand()` and signed `initData` are available before API bootstrap.

The `/app` command shows a WebApp button only when `MINIAPP_PUBLIC_URL` is configured. If absent, the bot replies that Mini App is unavailable.

On bot startup, the application registers Telegram's persistent chat menu button with `MenuButtonWebApp` when `MINIAPP_PUBLIC_URL` is present and HTTPS. Registration is idempotent and startup continues on temporary Telegram API errors.

## MAIN MINI APP SETUP

BotFather Main Mini App setup is manual after production deploy. Do not run it
from application code or deployment scripts.

1. Open `@BotFather`.
2. Select the production bot `@uchet_finbot`.
3. Open Bot Settings.
4. Configure Main Mini App.
5. URL: `https://app.kopipaste.ru`.
6. Name: `КопиPaste`.
7. Launch label: `Открыть`, if Telegram allows changing the label.
8. Save.
9. Open the bot profile.
10. Verify the system Launch/Open App button.
11. Verify the Apps section in Telegram Search.
12. Verify Telegram Desktop.
13. Verify Android.
14. Verify iOS.
15. Verify the chat-list surface where supported by the Telegram client.

The exact placement of Telegram's system launch button, including whether it is
shown directly in a chat-list row, is controlled by Telegram client version and
platform. The Bot API and `setChatMenuButton` cannot force that placement. Keep
the persistent `MenuButtonWebApp` with label `Открыть`, `/app`, and inline WebApp
button as supported entry points.

Main Mini App launch through `https://t.me/uchet_finbot?startapp` must use normal
Telegram `initData` authentication. The frontend may safely receive an empty or
unused `tgWebAppStartParam`; it is not authorization data and must not switch
workspace or expose another user's data.

Do not paste tokens or secrets into BotFather notes or deployment logs.

Dry-run documentation command:

```bash
MINIAPP_PUBLIC_URL=https://app.example.com .venv/bin/python - <<'PY'
from settings import MINIAPP_PUBLIC_URL
print("Configure BotFather menu button manually:", MINIAPP_PUBLIC_URL)
PY
```

Production browser smoke should be performed inside Telegram Desktop and on a
mobile Telegram client. Opening the URL directly in a normal browser must show
the safe message asking the user to open the application through the Telegram
bot button, and it must not call Mini App API bootstrap without `initData`.

Menu-button smoke:

- restart a staging bot with HTTPS `MINIAPP_PUBLIC_URL`;
- confirm startup logs contain successful menu-button registration without secret values;
- open the bot chat and press the persistent menu button;
- confirm the persistent menu button label is `Открыть`;
- confirm `/app` still renders the inline WebApp button and command list still includes `/start`, `/settings`, `/help`, `/app` when the Mini App URL is configured.
