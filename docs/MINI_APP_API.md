# Mini App API

All Mini App API responses use the same envelope:

```json
{
  "ok": true,
  "request_id": "uuid",
  "data": {}
}
```

Errors use:

```json
{
  "ok": false,
  "request_id": "uuid",
  "error": { "code": "bad_request", "message": "Request failed." }
}
```

## Authentication

Authenticated requests send Telegram WebApp `initData` with:

```http
Authorization: tma <initData>
```

`X-Telegram-Init-Data` is accepted as a fallback.

## Endpoints

- `GET /miniapp/health`
- `GET /miniapp/api/bootstrap`
- `GET /miniapp/api/workspaces`
- `GET /miniapp/api/overview`
- `GET /miniapp/api/operations`
- `POST /miniapp/api/operations`
- `GET /miniapp/api/operations/{id}`
- `PATCH /miniapp/api/operations/{id}`
- `DELETE /miniapp/api/operations/{id}`
- `GET /miniapp/api/analytics`
- `GET /miniapp/api/plans`
- `GET /miniapp/api/profile`
- `POST /miniapp/api/profile/theme`
- `POST /miniapp/api/analytics/event`

## Workspace Scope

Read endpoints accept:

- `workspace_id=<id>` for one accessible workspace.
- `workspace_id=all` for a read-only aggregate across accessible workspaces.
- omitted `workspace_id` for the active workspace.

Write endpoints require one concrete writable workspace. `all` is rejected for writes.

## Periods

Supported values:

- `current_month`
- `previous_month`
- `last_30`
- `custom` with `start_date` and `end_date`

Custom periods are capped at 366 days.

## Operation Create

Required fields:

- `workspace_id`
- `idempotency_key`
- `type`: `expense`, `income`, `Расходы` or `Доходы`
- `amount`: decimal string
- `category`
- `description`
- `op_date`: `YYYY-MM-DD`

The server derives actor identity from signed Telegram auth, not from the request body.

## Analytics Events

Allowed client events:

- `mini_app_tab_opened`
- `mini_app_workspace_changed`
- `mini_app_period_changed`
- `mini_app_transaction_add_opened`

Only safe coarse properties are accepted: `tab`, `period`, `scope`, `action`.
