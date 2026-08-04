# Finuchet Telegram Mini App Architecture

PR 1 adds the first production-ready Mini App surface without replacing the Telegram bot flows.

## Backend

- Package: `miniapp`.
- WSGI entrypoint: `miniapp.http:application`.
- Service boundary: `miniapp.api.MiniAppAPI`.
- Auth: Telegram WebApp `initData` validation in `miniapp.auth`.
- Operation mutations: Mini App update/delete call shared `services.operations.update_financial_operation` and `services.operations.delete_financial_operation`, the same mutation layer used by Telegram helpers.
- Storage additions:
  - `miniapp_user_preferences` for theme preference.
  - `miniapp_idempotency_keys` for safe transaction create retries.
  - `miniapp_rate_limits` for PostgreSQL-backed write limiting across WSGI workers.

The Mini App backend never trusts `user_id` from the browser. The authenticated Telegram user from signed `initData` is the only actor identity.

## Frontend

- Root: `frontend`.
- Runtime: Vite and TypeScript.
- App shell: `frontend/src/main.ts`.
- API client: `frontend/src/api.ts`.
- Money formatting: `frontend/src/money.ts`.

The first screen is Home, with five bottom tabs:

1. Operations
2. Analytics
3. Home
4. Plans
5. Profile

Home is centered in the bottom navigation.

## Scope

PR 1 includes:

- Bootstrap and authenticated API foundation.
- Workspace switching.
- Period switching.
- Home overview.
- Operations list/detail/create/edit/delete.
- Minimal read-only Analytics, Plans and Profile screens.
- Theme preference.
- Safe product analytics events.
- Existing category picker integration for create/edit.
- Confirmed delete and unsaved-change protection.

PR 1 does not include advanced charts, full budget editing, full goal editing, exports, onboarding redesign or push notification management inside the Mini App.

## Privacy

- Raw Telegram `initData` is not logged.
- Browser requests do not send bot tokens or server secrets.
- Product analytics properties use coarse UI context only.
- Financial text and descriptions are not included in product analytics events.

## Idempotency

Create requests must include a stable `idempotency_key`. The server hashes the request body and stores the idempotency row and financial operation in one database transaction. Same key and same hash replays the completed response without creating activity/product events again; same key and different hash returns `idempotency_conflict`; an active pending request returns `idempotency_pending`.

Pending requests carry a short server lease. If a worker stops before inserting an operation, the same key and payload can reclaim the stale pending row and retry. If recovery finds a completed operation id but no cached response, the API reconstructs the response from that operation and marks the idempotency row completed. A failure before response persistence rolls back the operation insert with the idempotency update.
