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
- `GET /miniapp/api/categories`
- `GET /miniapp/api/categories/manage`
- `POST /miniapp/api/categories`
- `PATCH /miniapp/api/categories/{token}`
- `DELETE /miniapp/api/categories/{token}`
- `GET /miniapp/api/overview`
- `POST /miniapp/api/insights/{id}/impression`
- `POST /miniapp/api/insights/{id}/feedback`
- `GET /miniapp/api/operations`
- `POST /miniapp/api/operations`
- `GET /miniapp/api/operations/{id}`
- `PATCH /miniapp/api/operations/{id}`
- `DELETE /miniapp/api/operations/{id}`
- `GET /miniapp/api/analytics`
- `GET /miniapp/api/analytics/category-structure`
- `GET /miniapp/api/analytics/time-dynamics`
- `GET /miniapp/api/analytics/radar`
- `GET /miniapp/api/plans`
- `GET /miniapp/api/goals`
- `POST /miniapp/api/goals`
- `GET /miniapp/api/goals/{id}`
- `PATCH /miniapp/api/goals/{id}`
- `POST /miniapp/api/goals/plan-preview`
- `POST /miniapp/api/goals/{id}/plan-preview`
- `POST /miniapp/api/goals/{id}/contributions`
- `POST /miniapp/api/goals/{id}/reminders`
- `POST /miniapp/api/goals/{id}/status`
- `GET /miniapp/api/limits`
- `POST /miniapp/api/limits`
- `PATCH /miniapp/api/limits/{id}`
- `DELETE /miniapp/api/limits/{id}`
- `GET /miniapp/api/reminders`
- `POST /miniapp/api/reminders`
- `PATCH /miniapp/api/reminders/{id}`
- `DELETE /miniapp/api/reminders/{id}`
- `POST /miniapp/api/reminders/{id}/record`
- `POST /miniapp/api/reminders/{id}/snooze`
- `GET /miniapp/api/profile`
- `GET /miniapp/api/profile/categories`
- `GET /miniapp/api/profile/notifications`
- `POST /miniapp/api/profile/notifications`
- `GET /miniapp/api/profile/premium`
- `GET /miniapp/api/profile/export`
- `POST /miniapp/api/profile/export`
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
- `current_week`
- `previous_month`
- `previous_year`
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

The `idempotency_key` must be generated when the form opens and reused for retrying the same create attempt. A completed request can be replayed without duplicating the operation or analytics/activity hooks. Reusing the key with a different request body returns HTTP 409 `idempotency_conflict`; an active in-flight duplicate returns HTTP 409 `idempotency_pending`. Stale pending rows are recovered by lease: retry creates the operation only if no operation was committed, or reconstructs the original operation response if an operation id already exists.

## Categories

`GET /miniapp/api/categories` accepts `workspace_id`, `type`, and optional `current_category`. Categories with the authenticated user's `relevant=false` preference are excluded from new selection by default; an existing hidden `current_category` remains available while editing.

Category lifecycle management lives under `GET /miniapp/api/categories/manage`, `POST /miniapp/api/categories`, `PATCH /miniapp/api/categories/{token}` and `DELETE /miniapp/api/categories/{token}`. Writes require one concrete writable workspace; aggregate `workspace_id=all` is read-only. Delete returns reference counts and requires a transfer target when operations or related limits, budgets, reminders, aliases or ML observations still point to the category. Protected system categories cannot be renamed or deleted.

`POST /miniapp/api/categories/{token}/preferences` accepts strict `priority=normal|high` and boolean `relevant`. Preference identity is authenticated user + nullable workspace + operation type + canonical category key. The management response always includes hidden categories so the user can restore them.

Categories are managed from Plans in the Mini App. Profile no longer shows category chips.

Reference counts for the management screen are batched per category list. The API must not open one connection or run all reference-table queries per category.

## Insights

`GET /miniapp/api/overview` includes `insights`, containing zero to three deterministic structured insights for one concrete authorized workspace and one currency. Each item carries a hashed `id`, stable detector type, period and comparison dates, structured evidence, and a bounded list of validated action types. `insight` remains the primary-item alias for Home compatibility.

The aggregate `workspace_id=all` scope intentionally returns no Insight Engine items in V1. Existing overview and Analytics totals remain available for that scope.

`POST /miniapp/api/insights/{id}/impression` records that an already-issued insight was displayed. `POST /miniapp/api/insights/{id}/feedback` accepts `feedback_type=useful|not_useful`. Both require the same concrete workspace scope and update only a pre-existing fingerprint owned by the authenticated user/workspace. The client cannot create arbitrary insight state through these endpoints.

## Analytics

`GET /miniapp/api/analytics` returns:

- `summary`: income, expense and result grouped by currency;
- `category_structure`: top categories plus `Прочее`;
- `time_dynamics`: backend-selected day/week/month grouping;
- `radar`: normalized category-share comparison against the previous period.

When multiple currencies are present, `aggregation_available=false` and the UI must not display one false total. The response includes `available_currencies` and `currency_groups`; category percentages, top-N and `Прочее` are calculated independently inside each currency group. Time dynamics returns one set of datasets per currency so the frontend never draws one line through RUB and EUR values.

`currency=RUB` is accepted as an optional chart filter after workspace access is checked. The value must be one of `available_currencies` for the authorized result set. Radar refuses mixed-currency data unless a concrete available currency is selected; in that case it returns `reason=mixed_currencies`, `insufficient_data=true`, and no axes.

## Goals

Goal endpoints reuse `services.goals`. Money fields are decimal strings. Goal create and contribution requests require an `idempotency_key`; replay with the same key and same body returns the completed response without creating duplicate entities or duplicate Mini App product events. Reusing the same key with a different body returns `idempotency_conflict`.

Goal create stores the goal row, optional initial movement, selected plan fields and Mini App idempotency completion in one database transaction. A crash cannot leave a committed goal with only a pending idempotency row.

Plan previews return backend calculations for deadline-first and comfortable-contribution modes plus `preview_payload_hash`, a deterministic binding over normalized plan-relevant fields. Create/update requests must include this hash. If any target/current amount, deadline, strategy, frequency, schedule, comfortable amount or reminders setting changes after preview, the backend returns HTTP 409 `goal_preview_stale`. Monthly, twice-monthly and weekly schedules require visible user-selected day fields; hidden defaults such as day 1, days 5/20 or Monday are not accepted by the Mini App API.

## Limits

Limit endpoints use existing category limits and general spending limits. MVP periods are `week` and `month`. Usage and statuses reuse backend Decimal calculations and `services.limit_alerts`; the frontend does not implement its own threshold policy.

General-limit and category-limit creation require an `idempotency_key`. Limit creation and Mini App idempotency completion commit in the same database transaction. Category-limit update uses one transaction for old-row removal and replacement. Delete checks the affected row count and returns `limit_not_found` for missing or foreign limits instead of reporting a false success. Changing a limit does not delete category-limit alert delivery history; current-period threshold eligibility therefore remains governed by the existing dedupe rows and operation IDs.

## Profile

Profile returns theme, spaces, notification preferences, authoritative `vacation_mode`, Premium info-only data, export entry metadata, help/privacy/terms links and version. Notification updates persist one setting at a time and emit sanitized product analytics only after backend confirmation.

Notification preferences expose grouped controls:

- `daily_notifications`: morning/evening daily reminders, with editable `morning_time` and `evening_time`;
- `plans_control`: limit, budget, goal, subscription and recurring-spend notifications;
- `reports`: weekly and monthly reports;
- `quiet_hours`: quiet-hour enabled/start/end state.

Challenge notifications are retired from Telegram delivery and are not exposed as a live Profile toggle.

`GET /miniapp/api/profile/behaviour` and `POST /miniapp/api/profile/vacation` read and update global Vacation Mode. Enabled mode requires valid ISO calendar start/end dates with `end >= start`; active/scheduled/completed status is resolved from the user's local date. Vacation skips proactive financial notifications without changing notification or Quiet Hours preferences. Explicit `user_reminder` delivery is unaffected.

`GET /miniapp/api/profile/privacy` exposes financial-export metadata, supported history periods, and shared-workspace semantics. `POST /profile/privacy/history/preview` must precede the confirmed `DELETE /profile/privacy/history` UX. Relative periods use the user-local date and all-history preview includes goals and drafts. Account preview uses `POST /profile/privacy/account/preview`; `DELETE /profile/privacy/account` requires `confirmed=true` and exact `confirmation_text=УДАЛИТЬ`. Successful account deletion is terminal and must not be followed by bootstrap or product-event writes.

`POST /miniapp/api/profile/export` supports `action=preview` and `action=send`. `send` builds an XLSX with the existing export builder and delivers it to the authenticated user's Telegram chat; no direct browser file download is returned. The shared XLSX builder separates every monetary total by currency across summary, operation, category, month and week sheets.

## Analytics Events

Allowed client events:

- `mini_app_tab_opened`
- `mini_app_workspace_changed`
- `mini_app_period_changed`
- `mini_app_transaction_add_opened`
- `mini_app_global_filter_opened`
- `mini_app_global_filter_applied`
- `mini_app_analytics_chart_filter_changed`
- `mini_app_analytics_grouping_changed`
- `mini_app_analytics_details_toggled`
- `mini_app_premium_opened`
- `mini_app_export_opened`
- `mini_app_home_challenge_opened`
- `mini_app_home_focus_opened`
- `mini_app_home_insight_opened`
- `insight_impression`
- `insight_opened`
- `insight_action_clicked`
- `insight_feedback`
- `mini_app_home_reminder_opened`
- `mini_app_challenge_carousel_changed`
- `mini_app_focus_carousel_changed`
- `mini_app_reminder_carousel_changed`
- `mini_app_profile_section_opened`
- `mini_app_profile_setting_changed`

Only safe coarse properties are accepted: `tab`, `period`, `scope`, `action`, `chart_type`, `filter_kind`, `period_kind`, `operation_type`, `has_category_filter`, `grouping`, `result`, `source`, `kind`, `setting`, `section`, `reminder_state`, `budget_kind`, `direction`, `position`, `total`.

## Advanced Forecasting

`GET /miniapp/api/overview` includes a compact `spendable` read model. A concrete scope returns approximate amount, currency, period/quality labels, risk state, fingerprint and feedback/experiment presentation state. `workspace_id=all`, completed periods and future-only periods return explicit unavailable objects and never a fake zero. Overview also returns separate `goal_items` and `limit_items`; aggregate scope receives an explicit `Выберите пространство` marker for each.

`GET /miniapp/api/forecast/spendable` accepts the normal workspace, period/custom dates and currency filters. It returns the authoritative breakdown, q50/q80/q90 variable forecast, budget headroom, expected period-end result, forecast band, reason codes, model/risk-policy versions and quality/calibration state. Expected future income is explanatory only and is excluded from the primary number.

`POST /miniapp/api/forecast/can-spend` accepts `workspace_id`, `currency`, period fields, positive `amount`, and optional `category`. Purchase date is not accepted because this release has no defensible date-sensitive decision policy. The server returns `fits`, `borderline`, `does_not_fit` or `insufficient_data` and the resulting Spendable/control impacts.

`POST /miniapp/api/forecast/{fingerprint}/feedback` accepts `workspace_id` and `feedback_type=useful|not_useful`. Duplicate feedback is protected by user/workspace/fingerprint uniqueness. `POST /miniapp/api/forecast/exposure` records only a validated rendered surface and quality tier; it cannot alter forecast policy.

Category-limit edit now locks and updates the original row in one transaction. Title, amount, category, period and alert preference persist in place. A target-key collision returns HTTP 409 `limit_conflict`; wrong scope returns `limit_not_found`.

The complete contract and privacy/model lifecycle are in `docs/FORECAST_INTELLIGENCE.md`.
