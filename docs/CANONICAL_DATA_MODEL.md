# Canonical Data Model

This branch adds a vendor-neutral analytics foundation. It does not remove or migrate legacy operational tables.

## Canonical Tables

- Users: `public.users`
- Operations: `public.operations`
- Personal and group workspaces: `public.workspaces`, `public.workspace_members`, `public.user_workspace_settings`
- Legacy single-user budgets: `public.budgets`
- General and grouped limits: `public.general_spending_limits`, `public.category_budget_groups`, `public.category_budget_group_members`, `public.category_limits`
- Reminders: `public.user_reminders`, `public.user_reminder_events`
- Notification preferences and operational delivery facts: `public.notification_preferences`, `public.notification_events`
- New product analytics: `analytics.product_events`
- New export outbox: `analytics.event_outbox`
- New acquisition attribution: `analytics.acquisition_attribution`
- New API cost and usage facts: `analytics.api_usage_events`
- New security monitoring facts: `security.security_events`

## Legacy Or Partial Tables

- `public.events`: legacy generic event storage. Do not write new generic product analytics here.
- `public.action_log`: legacy audit/action table. Do not reuse as product analytics.
- `public.financial_activity_events`: operational activity signal for reminders and inactivity logic. It remains required, but is not the generic analytics store.
- `public.notification_events`: operational notification fact table. It remains required for notification delivery/conversion state, but new generic analytics events should go to `analytics.product_events`.
- `public.notifications_log`: legacy notification log when present. Do not use for new generic analytics.

## Privacy Policy

Analytics IDs are pseudonymous HMAC-SHA256 identifiers generated with `ANALYTICS_HMAC_SECRET`. If the secret is missing, events may be stored locally with `external_export_allowed=false`; no weak fallback identifier is created.

Current deletion policy:

- Full account deletion suppresses pending outbox rows for that user.
- Product events younger than 180 days are deleted.
- Older product events are anonymized by removing `user_id`, `workspace_id`, `entity_id` and disabling external export.
- Attribution linkage is removed by clearing analytics identifiers and marking the row deleted.
- Financial history deletion does not remove unrelated behavior events, but removes links to deleted operation entities where possible.

## Storage Growth

With bounded JSONB properties, `analytics.product_events` is expected to use roughly 0.8-1.5 GB per one million rows including indexes. `event_outbox`, `api_usage_events` and `security_events` are expected to be smaller unless retained indefinitely.

## External BI

Views under `analytics.v_*` expose `analytics_user_id` and never expose raw Telegram user IDs. Internal tables may keep `user_id` only for local investigation, deletion and support workflows.
