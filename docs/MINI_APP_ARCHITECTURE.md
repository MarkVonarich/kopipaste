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

PR 2 completes the MVP surface for Analytics, Goals, Limits and Profile without replacing PR 1 architecture.

## PR 2 MVP

- Analytics uses backend datasets for summary totals, category structure, time dynamics and radar comparison. Chart rendering is isolated in the frontend; business math stays in the API.
- Multi-currency analytics is grouped by currency throughout the API. There is no implicit FX conversion, no mixed-currency radar, no mixed-currency category percentage structure and no line chart dataset spanning different currencies.
- Chart.js receives numeric coordinates only through `frontend/src/chartDecimal.ts`, a visual-only adapter for backend decimal strings. The original decimal string remains the display/tooltip value and is not used for financial decisions in the browser.
- Charts use `chart.js` from the lockfile for bar/line rendering plus a small local SVG radar view. Each chart keeps its own local filter state.
- Goals reuse `services.goals` for create/update/plan/contribution/reminder/status logic. Goal create uses Mini App idempotency keys, goal contributions use existing goal movement idempotency keys, and neither creates fake financial operations.
- Goal planning is preview-before-confirm: the frontend displays backend-calculated remaining amount, schedule, next contribution date and feasibility before save.
- Limits reuse category limits, general spending limits and `services.limit_alerts` threshold bands. Category/general limit mutations are owned by `services.miniapp_limits`; the API does not perform raw delete-plus-insert business SQL.
- Profile reuses existing workspace, category, notification preference, export and legal-link configuration. Premium is information-only in MVP.
- Product analytics go through the existing backend outbox and use only coarse, non-financial properties.

PR 2 still does not include challenges, recurring operations, credits, investments, bank integrations, subscriptions or payments.

## Privacy

- Raw Telegram `initData` is not logged.
- Browser requests do not send bot tokens or server secrets.
- Product analytics properties use coarse UI context only.
- Financial text and descriptions are not included in product analytics events.

## Idempotency

Create requests must include a stable `idempotency_key`. Public Mini App create actions namespace the key by entity/action, for example `operation:create:<key>`, `goal:create:<key>` and `limit:create:<key>`. Same key and same hash replays the completed response without creating activity/product events again; same key and different hash returns `idempotency_conflict`; an active pending request returns `idempotency_pending`.

Pending requests carry a short server lease. If a worker stops before inserting an operation, the same key and payload can reclaim the stale pending row and retry. If recovery finds a completed operation id but no cached response, the API reconstructs the response from that operation and marks the idempotency row completed. A failure before response persistence rolls back the operation insert with the idempotency update.

Operation creation stores the idempotency row and operation in one database transaction. PR 2 goal and limit create actions reuse the same durable idempotency table with replayable `response_json`; no additional idempotency table is required.
