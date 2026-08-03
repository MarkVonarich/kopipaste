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

- Open `Настройки -> Категории` and confirm the first screen offers only `Расходы`, `Доходы`, Back, and Main menu.
- Open expense and income category lists separately; confirm adding a category from each list preserves that type and does not ask for type again.
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

## Financial Goals

- Open Main menu and confirm `🎯 Цели` exists next to `🏆 Челленджи`.
- Confirm `Настройки -> Категории` no longer shows a Goals category placeholder.
- Open `🎯 Цели`.
- Create `✈️ Тестовый отпуск` with target `10 000 ₽`, deadline about two months ahead, and already saved `1 000 ₽`.
- Choose monthly plan and confirm a recommended contribution is shown.
- Confirm Back from Goals home, list, card, contribution, plan and edit screens returns one logical step.
- Enable reminders for the goal.
- Open `Настройки -> Оповещения` and confirm `Цели` reflects the global setting.
- Add a manual contribution and confirm progress plus next contribution recalculate.
- Withdraw part of the goal and confirm the plan recalculates.
- Adjust current balance and confirm an adjustment appears in movement history.
- Record a test salary income in a linked salary category and confirm one immediate goal suggestion appears.
- Press the salary suggestion contribution twice quickly and confirm only one goal movement is created.
- Use `Напомнить позже` and confirm a row enters the automatic notification system.
- Enable quiet hours around the snooze time and confirm the snoozed reminder is deferred once.
- Pause the goal and confirm scheduled reminders stop.
- Resume the goal, change the deadline and confirm the recommendation recalculates.
- Reach the target and confirm completion options are shown.
- Archive and restore the goal.
- Permanently delete a separate test goal and confirm ordinary linked operations remain.
- Confirm another user or workspace cannot access the goal.
- Confirm PostHog receives safe goal events without goal names, exact amounts, raw text or deadlines.

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

## Timezone Notifications and Limit Alerts

- Open `Настройки -> Оповещения -> Часовой пояс` and confirm the current IANA timezone is shown.
- Select Moscow, Kaliningrad, Ekaterinburg, Omsk, Krasnoyarsk, Irkutsk, Yakutsk, and Vladivostok in a staging account and confirm each saves as an IANA timezone.
- Select `Другая IANA`, enter a valid timezone such as `Europe/Stockholm`, and confirm it saves.
- Enter an invalid timezone and confirm the bot asks again without changing the saved timezone.
- Confirm changing timezone marks stale pending automatic notifications as skipped with reason `timezone_changed_stale_notification`.
- Set daily reminder hour to `20:00`; at a non-20 local hour, confirm the evening reminder job does not send or defer a stale evening reminder.
- Enable quiet hours crossing midnight and confirm reminders, reports, limits, challenges and goals respect the saved timezone rather than server time.
- Create or stage a category limit below 80% and confirm no limit alert is sent.
- Cross 80%, 90%, 100%, and exceeded states in a new period and confirm each alert has readable Russian copy with percentage, spent, limit, and remaining or exceeded amount.
- Confirm limit alert buttons open existing limit/settings screens and no dead callback appears.
- Confirm repeated scans in the same period do not resend the same category-limit band.
- Confirm safe analytics for `limit_threshold_reached` contain only threshold band, period, status, currency and source.

## Challenge Notifications Opt-In

- Open `Настройки -> Оповещения`.
- Confirm `Челленджи` is disabled after rollout.
- Open `Челленджи` manually and confirm all progress remains visible.
- Complete part of a challenge.
- Wait through an hourly scheduler scan.
- Confirm no challenge message arrives while challenge notifications are disabled.
- Complete a challenge.
- Confirm no unsolicited completion message arrives while disabled.
- Open `Челленджи` manually and confirm it is completed.
- Enable challenge notifications explicitly from `Настройки -> Оповещения -> Челленджи`.
- Confirm the bot acknowledges the setting immediately.
- Wait through multiple hourly scheduler scans.
- Confirm no more than one routine daily challenge reminder arrives.
- Complete another challenge.
- Confirm exactly one completion message arrives.
- Confirm no duplicate completion message arrives later.
- Enable quiet hours covering the test time.
- Confirm a routine stale challenge prompt is skipped.
- Confirm a meaningful completion notification is deferred only once.
- Disable challenge notifications again.
- Confirm future challenge notifications stop.
- Confirm normal reminders and reports remain operational.

## Backward Compatibility

- Existing personal records remain visible.
- Current OCR, voice, history, reports, budgets, limits, reminders, and Excel export still work.
