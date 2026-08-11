# Analytics Event Catalog

All product events are written to `analytics.product_events` with `event_version=1`. Allowed properties must pass the privacy sanitizer. Prohibited properties include raw user messages, OCR text, voice transcripts, images, Telegram usernames, phone numbers, API keys, prompts, responses, secrets, tokens and operation comments.

## User Lifecycle

| Event | Purpose | Trigger | Allowed properties | Entity | Status | Funnel | Retention |
|---|---|---|---|---|---|---|---|
| `bot_started` | Count starts and returning users | `/start` command | `is_new`, `has_start_payload` | none | `success` | acquisition, activation | delete/anonymize |
| `onboarding_started` | Reserved onboarding entry | first welcome screen | none | none | `started` | activation | delete/anonymize |
| `onboarding_completed` | Activation setup completion | onboarding finish | none | none | `success` | activation | delete/anonymize |
| `language_selected` | Locale adoption | locale selection | `locale` | none | `success` | activation | delete/anonymize |

## Operations

| Event | Purpose | Trigger | Allowed properties | Entity | Status | Funnel | Retention |
|---|---|---|---|---|---|---|---|
| `operation_started` | Reserved operation funnel start | reliable draft/free-text start | `source` | operation | `started` | operation funnel | delete/anonymize |
| `operation_created` | Successful financial operation | operation insert commit | `operation_type`, `category` | `operation` | `success` | activation, engagement | unlink on history deletion |
| `operation_failed` | Reserved failed create | reliable create failure | `error_code`, `source` | operation | `failed` | operation funnel | delete/anonymize |
| `operation_edited` | Operation edit | update helpers | `changed_fields` | `operation` | `success` | retention | unlink on history deletion |
| `operation_deleted` | Last-operation delete | delete callback | none | `operation` | `success` | retention | delete/anonymize |

## Budgets And Limits

Events: `budget_creation_started`, `budget_categories_selected`, `budget_created`, `budget_deleted`, `limit_created`, `limit_updated`, `limit_deleted`, `limit_threshold_reached`.

Purpose is budget/limit adoption and lifecycle. Trigger only from successful budget/limit flows. Allowed properties are `period`, `limit_kind`, `category_count`, `threshold`. Entity type is `budget`, `limit` or `category_budget_group`. Expected statuses are `started`, `success`, `reached`, `deleted`. These events support feature adoption and retention views.

## Reminders And Notifications

Events: `reminder_created`, `reminder_deleted`, `notification_sent`, `notification_clicked`, `notification_converted`.

Purpose is reminder and notification funnel measurement. Allowed properties are `notification_type`, `channel`, `dedupe_key_present`, `conversion_type`; never notification body. Entity type is `reminder` or `notification`. Expected statuses are `success`, `clicked`, `converted`, `deleted`.

## Export

| Event | Purpose | Trigger | Allowed properties | Entity | Status | Funnel | Retention |
|---|---|---|---|---|---|---|---|
| `export_started` | Export funnel start | export preview generated | none | `export` | `started` | export funnel | delete/anonymize |
| `export_completed` | Successful XLSX send | document sent | `row_count`, `period_days` | `export` | `success` | export funnel | delete/anonymize |
| `export_failed` | Failed XLSX send | export exception | `error_code` | `export` | `failed` | export funnel | delete/anonymize |

## Privacy

Events: `privacy_opened`, `financial_history_deleted`, `account_deleted`.

Purpose is privacy workflow observability. `financial_history_deleted` allows `operation_count`; `account_deleted` is local-only and must not be exported before identity removal. Entity is none. Retention follows the account deletion policy in `CANONICAL_DATA_MODEL.md`.

## Workspaces

Events: `workspace_created`, `workspace_joined`, `workspace_join_rejected`, `workspace_switched`.

Purpose is group/personal workspace adoption. Allowed properties are `workspace_kind` and safe status only. Entity type is `workspace`. Retention follows delete/anonymize policy.

## Mini App

Events: `mini_app_opened`, `mini_app_tab_opened`, `mini_app_workspace_changed`, `mini_app_period_changed`, `mini_app_global_filter_opened`, `mini_app_global_filter_applied`, `mini_app_transaction_add_opened`, `mini_app_transaction_created`, `mini_app_transaction_edited`, `mini_app_transaction_deleted`, `mini_app_theme_changed`, `mini_app_analytics_chart_filter_changed`, `mini_app_analytics_grouping_changed`, `mini_app_analytics_details_toggled`, `mini_app_activity_calendar_opened`, `mini_app_activity_opened`, `mini_app_goal_created`, `mini_app_goal_contribution_added`, `mini_app_goal_plan_changed`, `mini_app_budget_limit_created`, `mini_app_budget_limit_updated`, `mini_app_budget_limit_deleted`, `mini_app_budget_opened`, `mini_app_budget_created`, `mini_app_category_created`, `mini_app_category_renamed`, `mini_app_category_deleted`, `mini_app_plans_reminders_opened`, `mini_app_reminder_opened`, `mini_app_reminder_created`, `mini_app_reminder_recorded`, `mini_app_reminder_snoozed`, `mini_app_reminder_updated`, `mini_app_premium_opened`, `mini_app_notification_setting_changed`, `mini_app_export_opened`, `mini_app_export_sent`, `mini_app_home_challenge_opened`, `mini_app_home_focus_opened`, `mini_app_home_insight_opened`, `mini_app_home_reminder_opened`, `mini_app_challenge_carousel_changed`, `mini_app_focus_carousel_changed`, `mini_app_reminder_carousel_changed`, `mini_app_profile_section_opened`, `mini_app_profile_setting_changed`, `insight_impression`, `insight_opened`, `insight_action_clicked`, `insight_feedback`.

Insight event properties are limited to machine metadata such as `detector_type`, `surface`, `action_type`, and `feedback_type`. Raw transaction descriptions, merchant display text, financial amounts, workspace identifiers, and insight fingerprints are not event properties.

Purpose is Telegram Mini App adoption, navigation, transaction, analytics, goals, limits, reminders and profile funnel measurement. Allowed properties are coarse UI codes such as `surface`, `tab`, `period`, `scope`, `action`, `operation_type`, `has_category_filter`, `changed_fields`, `theme`, `chart_type`, `filter_kind`, `grouping`, `period_kind`, `kind`, `section`, `setting`, `result`, `source`, `reminder_state` and `budget_kind`. No user-entered text, operation descriptions, category names, reminder titles, goal names, limit names, exact financial values, exact custom dates, raw Telegram `initData`, tokens, secrets, prompts, responses or database identifiers.

Reserved compatibility events: `miniapp_opened`, `screen_viewed`, `form_started`, `form_completed`, `form_abandoned`, `error_shown`.

## Attribution

`acquisition_payload_rejected` records malformed `/start` or future `startapp` payload rejection with `reason=malformed_payload`. Valid attribution is stored in `analytics.acquisition_attribution`; first touch is immutable, last touch may update.

## Diagnostics

`posthog_connection_test` is queued only by the admin-only `/admin_posthog_test_event` command through the normal product-event and outbox path. Purpose: controlled PostHog connection verification. Trigger: admin command. Version: 1. Allowed properties: `test=true`; base properties include `source=admin_test` and `status=success`. Prohibited properties: all user text, IDs, names, tokens and secrets. Entity type: none. Funnel usage: none. Retention usage: operational diagnostics.

## Security Events

Security events are stored in `security.security_events` with severity `info`, `warning`, `high` or `critical`.

Initial events: `permission_denied`, `foreign_workspace_access`, `foreign_draft_access`, `invalid_callback`, `unknown_callback`, `admin_command_denied`, `group_join_rejected`, `rate_limit_exceeded`, `excessive_ocr_usage`, `excessive_voice_usage`, `excessive_exports`, `operation_velocity_spike`, `account_recreated_after_deletion`.

Allowed metadata: `handler`, `callback_prefix`, safe rule/status/error codes and numeric counters. Prohibited metadata: raw callback data when it may contain user content, raw message text, usernames, phone numbers, tokens and secrets.

## API Usage

API usage events are stored in `analytics.api_usage_events`, not `product_events`. Features are `voice_transcription`, `receipt_ocr`, `bank_screenshot_ocr`, `categorization`, `future_ai_assistant`. Token counts are nullable. Pricing returns `NULL` until a versioned pricing table or config is introduced.
