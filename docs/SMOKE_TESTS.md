# Telegram Smoke Tests

Run after deployment and restart.

## Commands

- Normal user command menu shows exactly `/start`, `/settings`, `/help`.
- `/help` opens the help area and does not mention hidden/admin commands.
- `/about` still opens the same help area.
- `/mlstats`, `/mltrain`, and `/admin_*` commands reject a non-admin user.

## Personal Operation

- Send `coffee 250`.
- Select an existing category.
- Repeat with a new/custom category where available.
- Confirm the operation is saved and the edit button opens amount/type/category/date/comment options.

## Voice and OCR

- Send a short voice message such as `coffee 250`.
- Confirm the operation saves and evening inactivity reminder is suppressed.
- Send a receipt/screenshot.
- Confirm selected operations save and evening inactivity reminder is suppressed.

## Group Operation

- In an unconfigured group, confirm the bot does not silently fail.
- After workspace migration/setup, confirm group operations do not appear in the user's personal workspace.
- Have two users start category selection at the same time and confirm drafts do not overwrite each other.

## Export

- Export today.
- Export last 7 days.
- Export last 14 days.
- Export current month.
- Export previous month.
- Export current year.
- Export previous year.
- Export a custom start/end period.
- Confirm every branch reaches XLSX generation, empty-period message, Back, or Cancel.

## Reminders

- Create a monthly payment.
- Confirm it appears in the reminder list.
- Confirm notification copy matches today/tomorrow correctly.
- Tap Record operation twice and confirm only one operation is recorded.
- Confirm recurring expense and income totals are shown separately by currency.

## Analytics, Notifications, Achievements

- Confirm dashboard/category services return workspace-filtered JSON in a staging shell.
- Confirm currencies are grouped, not added together.
- Confirm duplicate notification thresholds are not sent.
- Confirm an eligible achievement is awarded once.

## Backward Compatibility

- Existing personal records remain visible.
- Current OCR, voice, history, reports, budgets, limits, reminders, and Excel export still work.
