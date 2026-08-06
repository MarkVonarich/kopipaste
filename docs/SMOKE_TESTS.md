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
- Confirm voice input is enabled.
- Send `Магнит пятьсот семьдесят`.
- Confirm the operation amount is `570`.
- Send `Чижик двести шестнадцать рублей тридцать четыре копейки`.
- Confirm the operation amount is `216,34 ₽`.
- Send `Дикси тысяча`.
- Confirm the operation amount is `1 000`.
- Send unclear speech.
- Confirm the response says what was heard and gives a parse-specific example.
- Send a voice expense that crosses a category limit.
- Confirm the category-limit card appears immediately.
- Confirm typed operations still work.
- Send a receipt/screenshot.
- For a banking screenshot containing `Чижик -216,34 ₽` and `Дринкит -285 ₽`, confirm two candidates appear and both save.
- Confirm the receipt result says `✅ Готово: записано 2, пропущено 0. Сумма: 501,34 ₽`.
- Confirm cashback, card labels, UI buttons and day totals are not recorded as operations.
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
- Create test category `Продукты`.
- Set a weekly category limit to `2 000 RUB`.
- Add `1 000 RUB` expense and confirm the 50% card.
- Add `600 RUB` expense and confirm the 80% card.
- Add `200 RUB` expense and confirm the 90% card.
- Add another `200 RUB` expense and confirm the 100% card.
- Add `130 RUB` expense and confirm an exceeded card showing `2 130 / 2 000` and `130`.
- Add `150 RUB` expense and confirm another exceeded card showing `2 280 / 2 000` and `280`.
- Add decimal expenses that take the final spent amount to `2 280,35 RUB` and confirm the exceeded amount is `280,35 RUB`.
- Repeat the same update/callback and confirm no duplicate card appears for the same operation.
- Confirm normal scheduler scans do not resend exceeded cards.
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

## Decimal Money Migration

- In staging only, apply:
  `psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f migrations/20260803_015_decimal_operation_amounts.sql`
- Confirm existing integer operations still render without `,00`.
- Confirm new operation, category-limit and budget values with cents persist as `NUMERIC(18,2)`.
- Do not print API keys, bot token, PostHog token, HMAC secret or database URL during verification.

## Telegram Mini App PR 1

- Open the Mini App from Telegram and confirm it reaches Home first.
- Confirm Home shows income with Add income in the left column and expenses with Add expense in the right column.
- Confirm Home shows no more than three recent operations and `Все операции` opens the Operations tab.
- Confirm the daily challenge card shows progress and opens a handled Mini App route/plan surface.
- Confirm the focus card opens Goals or Limits according to the displayed card.
- Confirm the period insight is neutral/positive/warning and mixed-currency periods are not summed.
- Confirm Profile opens with the User accordion expanded and only one accordion section open at a time.
- Confirm Preferred name saves and a later bot confirmation uses that name.
- Confirm Currency saves for future defaults without altering existing operation history.
- Confirm Timezone saves a preset and a valid custom IANA value, and rejects an invalid value.
- Confirm Active workspace selection changes the default workspace and owner/admin workspace rename works.
- Confirm notification rows are switches, challenge notification preference remains available in Settings/Profile, and Quiet hours opens an editor instead of toggling directly.
- Confirm quiet-hours enabled/start/end save in one action and cross-midnight values still defer challenge notifications.
- Disable quiet hours after setting `23:00–09:00`, reopen the editor, and confirm `23:00–09:00` is still shown before enabling again.
- Confirm Telegram menu button `Открыть приложение` opens the Mini App when `MINIAPP_PUBLIC_URL` is HTTPS.
- Confirm `/app` still shows the inline WebApp button.
- Confirm the bottom navigation has exactly Operations, Analytics, Home, Plans and Profile.
- Confirm Home is the centered tab.
- Confirm every visible bottom tab opens a handled screen.
- Confirm Telegram Back closes an operation sheet or detail sheet before leaving the Mini App.
- Confirm Telegram light and dark themes apply without horizontal scrolling.
- Confirm choosing Telegram, light and dark theme in Profile persists after reload.
- Confirm bootstrap shows the authenticated user's currency and timezone.
- Confirm workspace switcher includes accessible personal and group workspaces.
- Confirm `Все пространства` is read-only.
- Confirm creating an operation is disabled or rejected in `Все пространства`.
- Confirm a writable concrete workspace allows a new expense.
- Confirm a writable concrete workspace allows a new income.
- Confirm Decimal amount `216,34` or `216.34` is saved and rendered as `216,34`.
- Confirm double tapping save creates one operation through idempotency.
- Confirm the Operations tab lists the new operation.
- Confirm operation detail opens from the list.
- Confirm editing amount, category, description and date updates the same operation.
- Confirm deleting the operation removes it from the list.
- Confirm a user cannot view a workspace they do not belong to.
- Confirm a user cannot edit or delete an operation from a foreign workspace.
- Confirm mixed-currency totals are shown separately, not added together.
- Confirm Analytics income, expense and result match operations for the selected period.
- Confirm Analytics workspace and period switches update all charts.
- Confirm category structure and dynamics charts render at 320 px and in dark/light themes.
- In a staging account with RUB and EUR operations, confirm the Mini App says `Валюты показаны отдельно. Автоматическая конвертация не выполняется.`
- Confirm category structure has a currency selector and percentages are 100% only inside the selected currency.
- Confirm dynamics has a currency selector and does not connect RUB and EUR in one line.
- Confirm Radar compares the selected period with the previous period and explains normalized values.
- Confirm Radar shows an explanation, not a polygon, when data is insufficient.
- Confirm Radar does not render mixed currencies and works only after selecting one available currency.
- Confirm a period with no current operations but mixed-currency previous-period operations still shows Radar currency separation instead of a mixed polygon.
- Confirm each chart menu changes only that chart's local filter.
- Confirm different currencies are not silently added together.
- Confirm Plans switches between Goals and Budgets/Limits.
- Confirm creating a goal first shows a backend plan preview and does not save before explicit confirmation.
- After preview, change target/deadline/schedule/reminders and confirm the save confirmation disappears until preview is refreshed.
- Confirm deadline mode preview shows remaining amount, selected deadline, selected frequency, contribution count, recommended contribution, next date and feasibility.
- Confirm comfortable amount mode preview shows comfortable contribution, frequency, required contribution count, next date and projected completion date.
- Confirm monthly schedule requires a visible day 1-28.
- Confirm twice-monthly schedule requires two visible ordered days 1-28.
- Confirm weekly schedule requires a visible weekday.
- Confirm reminder-enabled goal plans require a visible schedule and preserve quiet-hours/timezone behavior.
- Confirm retrying the same goal create after a timeout reuses the same idempotency key and creates one goal.
- Confirm goal contribution uses one idempotency key and double submit does not create two movements.
- Confirm creating a general limit twice with the same idempotency key creates one limit.
- Confirm editing a category limit across category/period either stores the new limit or leaves the old limit after an injected failure.
- Confirm deleting a missing or foreign limit shows not found rather than success.
- Confirm deleting a limit uses the Mini App confirmation dialog.
- Confirm pause/resume/archive goal actions work only in a writable workspace.
- Confirm goal reminders are opt-in and quiet-hours aware through the central notification system.
- Confirm creating, editing and deleting a limit works for week/month periods.
- Confirm limit usage/status reflects server policy for 50/80/90/100 and exceeded states.
- Confirm disabling limit notifications does not delete the limit.
- Confirm Profile opens without exposing secrets or identifiers beyond the signed-in user context.
- Confirm Profile shows theme, spaces, categories, notifications, Premium, export/data, help, privacy, terms and version.
- Confirm notification toggles save one setting without changing adjacent settings.
- Confirm Premium is information-only and has no payment/paywall.
- Confirm export entry uses the existing export flow and does not change deletion policy.
- Confirm the additional menu hides unsupported add-to-home and includes share/help/report/privacy/terms/version.
- Confirm product events are present for Mini App open, tab, workspace, period and transaction actions.
- Confirm product events are present for chart filter, goal, limit, Premium, notification setting and export actions after backend confirmation.
- Confirm product events do not include raw financial text, descriptions, category names, goal names, limit names, exact dates, Telegram initData, tokens or database identifiers.
- Confirm the Home card shows `Доходы − Расходы`, not a bank balance.
- Confirm the Home `+` opens one sheet with only supported actions: `Расход` and `Доход`.
- Confirm delete requires a confirmation dialog and cancellation does not delete.
- Confirm closing dirty create/edit sheets asks before discarding changes.
- Confirm retry after a simulated timeout reuses the same idempotency key and does not duplicate the operation.
- Confirm immediate double submit either replays the completed operation or returns `idempotency_pending`, and does not emit duplicate post-commit effects.
- Confirm `/app` shows the Mini App button only when `MINIAPP_PUBLIC_URL` is configured.
- Confirm `./scripts/miniapp_production_like_smoke.sh --dry-run` passes in staging before production wiring.

- Existing personal records remain visible.
- Current OCR, voice, history, reports, budgets, limits, reminders, and Excel export still work.
