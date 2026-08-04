# Mini App Deployment

PR 1 introduces code and a migration only. Do not apply this migration automatically from a feature branch.

## Migration

Production command, when approved:

```bash
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f migrations/20260804_016_miniapp_foundation.sql
```

Rollback for the new Mini App tables only:

```sql
DROP TABLE IF EXISTS public.miniapp_idempotency_keys;
DROP TABLE IF EXISTS public.miniapp_user_preferences;
```

The operation indexes created by the migration are additive and can be dropped separately if a database rollback plan requires it.

## Service Integration

The WSGI app is exposed as:

```text
miniapp.http:application
```

Production hosting should terminate TLS before Telegram opens the Mini App URL. Configure the frontend build output and WSGI route in infrastructure, not in application secrets.

## Environment

Required existing variables:

- `TELEGRAM_TOKEN`
- `DATABASE_URL`

Optional:

- `MINIAPP_VERSION`
- `MINIAPP_INITDATA_MAX_AGE_SECONDS`

Do not modify PostHog, systemd unit files or production secrets as part of PR 1.

## Release Checks

- Backend compile succeeds with `miniapp`.
- Mini App imports succeed.
- Python tests pass.
- Frontend typecheck, lint, tests and build pass.
- Bot smoke check still passes.
- Telegram Mini App opens inside Telegram with signed `initData`.
