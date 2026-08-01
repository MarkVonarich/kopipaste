# Financial Goals

Financial Goals are flexible saving plans, not expense categories and not bank transfers.

## Product Model

A goal answers:

- where the user is now: current accumulated balance;
- what to do next: next practical contribution;
- what changes after edits, contributions or withdrawals: recalculated plan and status.

Goal contributions are internal allocation movements. They do not create ordinary income or expense operations and do not affect normal financial totals.

## Lifecycle

Goals can be active, achieved, paused, archived or permanently deleted. Archived and paused goals do not generate automatic reminders or salary suggestions. Archived goals keep history and can be restored. Permanent deletion removes only goal-owned rows and pending goal notifications; linked ordinary operations remain.

## Planning

Supported strategies:

- deadline first: target, current balance, deadline and schedule produce a recommended contribution;
- comfortable contribution first: target, current balance, comfortable contribution and schedule produce a projected completion date;
- no plan: manual contributions and withdrawals still work.

Supported frequencies:

- one monthly contribution;
- two monthly contributions;
- weekly contribution;
- salary-linked monthly or twice-monthly contribution;
- no schedule.

Deadline-first calculations count future eligible occurrences through the deadline inclusively and round the required contribution upward to the currency minor unit. Contribution-first calculations use the smallest whole number of planned contributions needed.

## Ledger

`goal_movements` is the authoritative audit trail. Movement types are:

- `initial`;
- `contribution`;
- `withdrawal`;
- `adjustment`.

The cached goal balance is updated in the same transaction as each movement. Withdrawals cannot make the balance negative. Idempotency keys prevent repeated callback presses from duplicating movements.

## Reminders

Goal reminders use `automatic_notifications`; background jobs never call `bot.send_message` directly for goal reminders. Delivery requires:

- global `goal_notifications_enabled`;
- per-goal `reminders_enabled`;
- active goal status;
- a relevant current occurrence;
- dedupe key not already queued/sent/skipped;
- quiet-hours policy allowing delivery or deferral.

Planned contribution reminders use `DEFER`. Stale or obsolete reminders are suppressed when a goal is paused, archived, achieved or deleted.

## Salary Integration

Salary-linked plans use explicitly selected income categories. When a confirmed income operation matches a linked category and compatible currency, the bot can show an immediate interactive suggestion. This is a direct response to the user action and may appear during quiet hours. Accepting the suggestion creates an internal goal contribution linked to the source operation and uses an idempotency key. Editing or deleting the source operation later does not silently reverse the goal movement.

## Workspace And Currency

V1 goals belong to an owner user and active workspace. Only the owner can edit, move money, pause, archive or delete. Service-layer mutations validate owner and workspace. Goal currency is fixed; V1 does not silently convert historical goal movements.

## Privacy And Analytics

Account deletion removes goals, movements, drafts and pending goal notifications. Full financial-history deletion also removes goals so cached balances cannot outlive a deleted ledger. Partial operation deletion leaves goal movement history intact and linked operation references can detach.

PostHog events use the local product-event outbox only. Safe properties include strategy, frequency, status, reminder flag, source, progress bucket and currency. Goal names, exact amounts, deadlines, raw text, chat IDs and salary amounts are not included.

## Migration

Future production command:

```bash
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f migrations/20260801_014_financial_goals.sql
```

Rollback by dropping the added tables and notification preference column is only acceptable before real goal movements exist. After production usage starts, destructive rollback requires explicit product approval because it loses goal plans and ledgers.

## Future Roadmap

Deferred areas: bank transfers, automatic salary distribution, bank balance sync, investment returns, cross-goal optimization, multi-goal what-if simulations, shared competitive goals, AI financial advice, automatic FX conversion, Mini App and social features.
