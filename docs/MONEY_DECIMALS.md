# Money Decimal Support

Finuchet supports monetary amounts with up to two decimal places in typed text,
voice-normalized text and receipt/OCR candidates.

Examples:

- `Магнит 570` -> `Decimal("570.00")`
- `Чижик 216,34` -> `Decimal("216.34")`
- `Кофе 199.9` -> `Decimal("199.90")`
- `Покупка 1 250,50` -> `Decimal("1250.50")`

Values with more than two decimal places or malformed separators are rejected.
Dates such as `03.08` and `03.08.2026` are parsed as dates when used as a
trailing operation date, not as money.

## Storage Audit

- `public.operations.amount`: migrated to `NUMERIC(18,2)`.
- `public.category_limits.amount`: migrated to `NUMERIC(18,2)`.
- `public.budgets.week_limit` and `public.budgets.month_limit`: migrated to `NUMERIC(18,2)`.
- `public.general_spending_limits.amount`: migrated to `NUMERIC(18,2)`.
- `public.category_budget_groups.amount`: migrated to `NUMERIC(18,2)`.
- `public.user_reminders.amount`: already `NUMERIC(14,2)`.
- `public.financial_goals.target_amount`, `current_balance`, `comfortable_amount`,
  `planned_contribution_amount`: already `NUMERIC(14,2)`.
- `public.goal_movements.amount` and `balance_after`: already `NUMERIC(14,2)`.
- `public.action_tokens.amount_minor`: remains `BIGINT` minor units for old pending-op tokens.
- `public.operation_drafts.payload.amount`: JSON payload; new drafts store a decimal string,
  old integer drafts remain readable.
- Receipt candidates are kept in memory and serialized in user state as decimal strings.
- Reports, limits and exports aggregate with `Decimal`; analytics/product-event metadata
  must not include exact financial amounts.

## Migration

Do not apply this migration automatically from application code. In staging or
production maintenance, run:

```bash
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f migrations/20260803_015_decimal_operation_amounts.sql
```

The migration is idempotent and preserves existing integer values exactly:
`285` becomes `285.00`.
