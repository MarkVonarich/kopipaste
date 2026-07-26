# PostHog Outbox Exporter

## Architecture

Finuchet exports product analytics only through PostgreSQL:

Telegram bot -> `analytics.product_events` -> `analytics.event_outbox` -> PostHog exporter -> PostHog Cloud EU.

PostgreSQL remains the source of truth. Telegram handlers never call PostHog directly, and this release does not use frontend autocapture or the PostHog browser SDK.

## Environment Variables

- `POSTHOG_PROJECT_TOKEN`: project token. Never log or print it.
- `POSTHOG_HOST`: HTTPS origin only, for production EU use `https://eu.i.posthog.com`.
- `POSTHOG_EXPORT_ENABLED`: defaults to `false`.
- `POSTHOG_EXPORT_BATCH_SIZE`: defaults to `50`.
- `POSTHOG_EXPORT_INTERVAL_SECONDS`: defaults to `60`.
- `POSTHOG_EXPORT_TIMEOUT_SECONDS`: defaults to `10`.
- `POSTHOG_EXPORT_MAX_ATTEMPTS`: defaults to `8`.
- `POSTHOG_EXPORT_MAX_EVENT_AGE_DAYS`: defaults to `30`.

Missing token, missing host, invalid host or non-HTTPS host disables export safely. Host configuration may not include paths, query strings or fragments.

## Endpoint

The exporter posts to `{POSTHOG_HOST}/batch/`. With EU production settings this is `https://eu.i.posthog.com/batch/`.

## Batch Mapping

Request body:

```json
{
  "api_key": "<POSTHOG_PROJECT_TOKEN>",
  "historical_migration": false,
  "batch": []
}
```

Each item contains `event`, `timestamp` and `properties`. Properties include `distinct_id` from `analytics_user_id`, `event_uuid`, `event_version`, `event_group`, `source`, `platform`, optional locale/currency/status/workspace/entity metadata, and sanitized event properties.

Never exported: raw `user_id`, Telegram IDs, chat IDs, usernames, display names, phone numbers, raw messages, operation comments, exact amounts, OCR text, voice transcripts, image data, prompts, responses, database URLs, tokens or secrets.

## Retry And Dead Letter

The exporter claims rows with `FOR UPDATE SKIP LOCKED`, commits, sends HTTP outside the DB transaction, then marks rows sent or failed in a short transaction.

2xx responses are sent. Timeouts, connection errors, 429 and 5xx responses retry with existing exponential scheduling. Permanent local malformed events and 400/401/403/413 responses are not retried forever and become dead-letter according to exporter failure handling.

Delivery is at-least-once. `event_uuid` is the stable event identity in PostHog properties. Ambiguous HTTP results are never treated as accepted.

## Suppression

Rows are suppressed when the product event is deleted, export is not allowed, `analytics_user_id` is missing, or `occurred_at` is older than `POSTHOG_EXPORT_MAX_EVENT_AGE_DAYS`.

Account deletion suppresses pending outbox rows and deletes/anonymizes analytics linkage through the analytics privacy policy.

## Scheduler

`posthog_outbox_export` is registered only when configuration is valid and `POSTHOG_EXPORT_ENABLED=true`. Runs are bounded, non-overlapping in-process, and safe with future multiple workers because row claiming uses `SKIP LOCKED`.

Safe log summary only:

```text
posthog_export: claimed=20 sent=20 retried=0 dead_letter=0 skipped=0 duration_ms=340
```

## Manual Commands

Read-only status:

```bash
.venv/bin/python scripts/posthog_export_status.py
```

Dry-run preview, no network and no token output:

```bash
.venv/bin/python scripts/posthog_export_once.py --limit 10
```

Explicit send:

```bash
POSTHOG_EXPORT_ENABLED=true .venv/bin/python scripts/posthog_export_once.py --send --limit 10
```

Controlled one-event send:

```bash
POSTHOG_EXPORT_ENABLED=true .venv/bin/python scripts/posthog_export_once.py --send --event-uuid <uuid>
```

## Admin Verification

- `/admin_posthog_status`: safe counts only.
- `/admin_posthog_test_event`: queues `posthog_connection_test` through the normal product-event and outbox path. It does not call PostHog directly.

## Enable Procedure

1. Confirm analytics migration is applied.
2. Confirm `ANALYTICS_HMAC_SECRET`, `POSTHOG_PROJECT_TOKEN` and `POSTHOG_HOST=https://eu.i.posthog.com`.
3. Run `scripts/posthog_export_status.py`.
4. Queue `/admin_posthog_test_event`.
5. Run dry-run `scripts/posthog_export_once.py --event-uuid <uuid>` if verifying manually.
6. Set `POSTHOG_EXPORT_ENABLED=true` through the normal environment management path.
7. Restart through the normal release procedure.
8. Check `/admin_posthog_status`.

## Disable Procedure

Set `POSTHOG_EXPORT_ENABLED=false` through environment management and restart through the normal release procedure. Pending rows remain in PostgreSQL.

## Rollback

Revert exporter code and disable `POSTHOG_EXPORT_ENABLED`. Do not delete `analytics.product_events`; PostgreSQL remains the source of truth. Dead-letter/suppressed rows can be inspected later.

## Secret Rotation

Rotating `POSTHOG_PROJECT_TOKEN` affects only exporter authentication. Rotating `ANALYTICS_HMAC_SECRET` changes future pseudonymous IDs; plan that separately to avoid splitting identities.

## Mini App

Mini App events will use the same `track_product_event` and `analytics.event_outbox` path, with no direct frontend PostHog autocapture in this architecture.
