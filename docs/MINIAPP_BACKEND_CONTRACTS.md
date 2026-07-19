# Mini App Backend Contracts

Status: prepared backend contracts only. No Telegram Mini App frontend or public HTTP API is implemented in this phase.

## Authentication Boundary

Future Mini App requests must verify Telegram Mini App init data server-side before any workspace or finance data is returned. The verified Telegram user id becomes the actor id. Every request must resolve:

- actor user id;
- requested workspace id;
- workspace membership and role;
- permission for the requested action.

Do not trust workspace ids, user ids, roles, or display names sent by the client.

## Workspace DTO

```json
{
  "workspace_id": 1,
  "name": "Personal",
  "kind": "personal",
  "role": "owner",
  "active": true
}
```

Services:

- `services.workspaces.list_accessible_workspaces(user_id)`
- `services.workspaces.set_active_workspace(user_id, workspace_id)`
- `services.workspaces.resolve_workspace(chat_id, actor_user_id, chat_type)`

## Operation DTO

```json
{
  "operation_id": 123,
  "workspace_id": 1,
  "actor_user_id": 42,
  "user_id": 42,
  "chat_id": 42,
  "amount": 250,
  "currency": "RUB",
  "type": "Расходы",
  "category": "Coffee",
  "operation_date": "2026-07-19",
  "source": "text",
  "comment": "From Telegram"
}
```

Canonical write service:

- `services.operations.record_financial_operation(...)`

Allowed sources: `text`, `voice`, `ocr`, `reminder`, `import`, `miniapp`, `api`.

## Analytics DTOs

Home dashboard:

- `services.analytics.dashboard_summary(workspace_id, user_id, today=None)`
- returns period metadata, per-currency totals, net cash flow, operation count, recent operations.

Time series:

- `services.analytics.time_series(workspace_id, user_id, start, end, bucket="day")`
- bucket is `day`, `week`, or `month`.

Categories:

- `services.analytics.category_analytics(workspace_id, user_id, start, end, op_type="Расходы")`
- returns totals grouped by category and currency.

Currencies are grouped. The backend must not silently add RUB, USD, EUR, or other currencies together.

## Settings Areas

Future Settings screen sections:

- workspaces;
- notifications;
- budgets;
- limits;
- categories;
- account/privacy.

## Reminders/Commitments

Reminder rows must be filtered by workspace id and actor permissions after migration `20260719_009_pre_miniapp_foundation.sql` is applied.

## Privacy

Voice and OCR flows may use external processing providers depending on deployment configuration. The Mini App must show privacy information before exposing account deletion/export controls.
