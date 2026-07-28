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
- From a weekly report, tap `Экспорт за неделю` and confirm the XLSX period matches the report.
- From a monthly report, tap `Экспорт за месяц` and confirm the XLSX period matches the report.

## Category and Budget Management

- Open `Настройки -> Категории` and confirm the first screen offers `Расходы`, `Доходы`, `Цели`, Back, and Main menu.
- Open expense and income category lists separately; confirm adding a category from each list preserves that type and does not ask for type again.
- Open `Цели` and confirm it is informational only and does not create category records.
- Create a temporary expense category and a temporary income category.
- Create an operation in the temporary expense category.
- Add a weekly or monthly budget.
- Open the budget card and edit the budget amount.
- Confirm the edit preview before saving.
- Delete the budget and confirm operations/categories are still present.
- Open the temporary category card and confirm it shows type, operation count, budget/limit, reminders, and ML mappings.
- Rename a category with case-only changes and confirm the category remains usable.
- Try renaming a category to an existing same-type category; confirm the duplicate screen offers transfer/merge, enter another name, open existing, Back, and Cancel.
- Transfer temporary expense category records to another expense category.
- Confirm history and reports show the destination category.
- Confirm income category transfer only offers income destinations.
- Delete/archive the empty source category after transfer.
- Confirm the source category no longer appears in operation selectors.
- Delete a category with operations by choosing transfer records and delete; confirm the destination budget is not silently overwritten.
- Delete another temporary category with operations by choosing permanent deletion; confirm two separate confirmations are required before operations are removed.
- Confirm protected/system categories such as `Без операций` cannot be renamed or deleted.
- Trigger or preview an evening reminder and confirm exactly one feature tip appears.
- Tap the feature-tip CTA and confirm it opens the matching bot flow.
- Check Back and Cancel buttons for each budget/category step.

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
