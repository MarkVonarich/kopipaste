# Cashflow Preview And Category Starter

## Existing Product

The repository contains a Python Telegram bot (`main.py`, `routers`), a signed-Telegram Mini App API (`miniapp`), a Vite/TypeScript frontend (`frontend`), and PostgreSQL services. Operations, goals, limits, reminders, category management and personalized suggestions already exist. The older `README` inventory referred to a documentation-only snapshot and was no longer accurate.

The existing Home `Свободно` calculation uses the selected period's realized income less expenses and forecast reserves. It is not an account balance. Its calculation and budget rules remain intact.

## Delivered Vertical Slice

`services.cashflow` provides a separate, read-only cash scenario. Mini App Home opens `Деньги до даты`; Telegram `/forecast` calls the same service. No balance, financial operation or forecast snapshot is written by the new scenario endpoint. Financial amounts are sent in the authenticated POST body, not in URLs or product analytics.

The user confirms their current total cash across their own accounts and cash holdings **in one currency**. Each new opening starts with an empty balance. The scenario is explicitly manual: after a new operation, enter the new actual balance. It does not reuse period net income as an opening balance or cache a stale balance between tabs. A stale response cannot replace edited form inputs.

Horizons: next scheduled income, local month end, or a specific date up to 366 days ahead. The next-income horizon needs a real, unpaid income reminder with a date. Group/all-workspace scopes are unavailable for this initial cash-account feature.

```text
ordinary = mean variable expense per tracked historical day * forecast days
can_spend_now = max(0, confirmed_balance - protected_reserve
                      - unpaid_planned_expenses - future_goal_reserve - ordinary)
daily_allowance = max(0, confirmed_balance - protected_reserve
                        - unpaid_planned_expenses - future_goal_reserve) / days
```

Amounts use Decimal. The horizon includes the remaining current calendar day conservatively as one full average-spending day. Daily allowance is rounded down to cents. The daily table separately shows balances without new income and with expected income. Expected income never increases `can_spend_now`. Negative balances remain visible; only the additional spendable amount is floored at zero. Exhaustion is explicitly the scenario without new income and is bounded by the chosen horizon.

Only explicit active reminders enter dated cash events. Weekly/monthly/yearly/custom-day recurrences reuse the reminder schedule engine, including its existing month-end clipping convention. A `(reminder_id, occurrence_date)` marked `recorded` is excluded, while later recurrences remain. Equal date/amount alone never deduplicate different payments. Overdue unpaid expenses are reserved immediately; overdue income is excluded until its date is clarified. Deleted/inactive reminders do not create future events.

Future goal contributions reuse the existing schedule engine and remaining-target cap. Goals protect money rather than debit the daily cash balance; future goal reserves are deducted only once from spendable cash. Already saved funds must be included in the manually specified protected reserve. Category/general budgets remain separate constraints; the scenario is not permission to exceed a budget.

The mean pace needs at least 14 tracked dates in the last 60 completed days and at least 35% date coverage since the first included observation. Only expenses and explicit no-operation markers count as tracked days. Missing dates and income-only dates are not zero-spend evidence. Reminder-created and goal-linked expenses are excluded from ordinary pace to avoid reserving them twice. A genuine history of explicit zero-spend days can support a zero pace. Missing evidence produces a null spendable amount and an explanation.

## Categories

The existing `custom_categories.icon` and `.color` columns store whitelisted identifiers and palette values. No external artwork or arbitrary SVG/CSS is accepted. The palette and icons reuse the project's own icon system. Names remain visible text. Create/edit has live preview; saving just appearance leaves operations and category references untouched. Renaming still uses the transactional category lifecycle service, with appearance saved in the same transaction.

The starter has 8 expense and 5 income categories. `Другое` is used instead of the proposed `Прочее` to match the existing suggestion fallback; `Продукты` and `Зарплата` retain existing names. Names are normalized with the same canonical key used elsewhere.

`ensure_user` inserts the starter only when its `INSERT users ... ON CONFLICT DO NOTHING` creates the user. The personal workspace, membership, active workspace and categories commit together with that insert. Rollback leaves registration retryable. Existing users, including users with no operations, receive no automatic additions. Suggestions, per-user relevance/priority, rename, hiding and creation continue using the existing services. Historical operations are not reclassified by initialization.

## Manual Acceptance

1. In a local/test Mini App, open Home -> Деньги до даты. Try each horizon; leave balance empty and verify that no number is invented.
2. Enter positive, zero and negative balances. Set a reserve. Check that future income appears separately and does not increase currently spendable cash.
3. Add test recurring expense/income reminders. Record one occurrence through the existing reminder action, update the actual balance, and recalculate: that occurrence must disappear, while the next recurrence remains.
4. Try overdue expense/income reminders, a missing next-income date and insufficient spending history. Confirm explicit explanations.
5. Change a field while a forecast request is pending; its old response must not appear against the new input. Close/reopen the screen and check that the balance is empty.
6. Use `/forecast 10000 месяц 2000` in a test private chat. Compare with the Mini App using the same scope, currency, date, history and balance. Group chats should direct the user to private chat without exposing money.
7. Plans -> Categories -> Create/edit: change name, icon and color; verify preview, save, reopen, then rename and hide from suggestions. Old reports keep their category text and amounts.
8. Register a fresh test user from the bot or Mini App: 13 starter categories in their personal workspace, once. Repeat registration and test an existing user: no additions.

## Boundaries And Next Step

This is a completed preview slice, not a new account ledger. Automatic persistent balances, individual accounts, reconciled opening balances, durable own-account transfers and group cash ownership remain a separate stage. The pure cash model ignores explicit transfer events; the current application has no supported transfer-creation workflow. Previously entered transfers masquerading as ordinary income/expenses cannot be reliably inferred and are not rewritten.

Unlinked manual payments cannot be matched to reminders from date/amount alone: record/confirm the corresponding reminder before relying on the scenario. Statistically detected subscriptions and recurring patterns are not added as a second set of dated cash liabilities; their ordinary expense history continues to contribute to pace. A later stage can introduce explicit confirmation/linking and account reconciliation, then automatically invalidate and recalculate persisted forecasts on mutations.

No schema migration is required for this slice. No production migrations, service restart, deployment or GitHub push are part of this local task.
