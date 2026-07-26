# Personalized Notifications and Budgets Progress

Branch: `codex/personalized-notifications-and-budgets`

## Phase 0 Audit Snapshot

Started from latest `origin/main` at `081ae4f`.

Current root causes found before implementation:

- Main navigation has a centralized `main_menu_kb`, but it still exposes old budget wording and the settings menu exposes category limits as a separate peer instead of routing through one "Limits and budgets" hub.
- Budget behavior is split between legacy weekly/monthly `bud_*` callbacks and category-limit `lim_*` / `cl_*` callbacks inside the large callback router. There is no reusable backend model for general limits or combined category budgets.
- Category-limit calculations are user/chat centric in several helpers and do not consistently use workspace/timezone boundaries.
- Limit alerts are spread across daily-job helper logic and category-limit state fields; alert text does not share a renderer and threshold dedupe is limited.
- Notification jobs still prioritize random generic morning/evening templates. Existing `services.notifications` only creates inactivity candidates and a generic queue; it lacks deterministic personalized facts, quiet-hour checks, and per-feature preferences.
- Notification preferences exist, but morning/evening enablement and feature-specific notification toggles are incomplete and inconsistent in UI.
- Subscription and recurring-spend detection are not modeled; reminder data exists but does not feed a prediction engine.
- Operation editing still works mostly on "last operation" context. Some edit paths are stable enough to update in place, but the UI does not consistently carry an operation id, leaving room for wrong-record edits.
- Export currently preserves source as raw enum-ish values and historical comments such as `From Telegram` can leak into exported comments.
- Weekly/monthly reports have basic comparisons but no stable hashtags, category movement detail, budget context, or recurring-spend facts.
- Message cleanup is ad hoc; there is no classification for transient UI versus persistent confirmations/reports/alerts.
- New UI labels are often hardcoded Russian strings rather than routed through `services.i18n`.
- Callback data is compact in some new flows, but older callbacks still embed category names in several paths.

## Implementation Notes

- Do not build Telegram Mini App UI in this branch.
- Additive migrations only; do not apply migrations from Codex.
- Keep reusable services testable without a live database where possible.
