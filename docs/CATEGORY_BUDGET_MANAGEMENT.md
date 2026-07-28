# Category and Budget Management

This release improves Telegram-only management for budgets and categories.

## Budget Editing and Deletion

Users can open budgets from:

- `Главное меню -> Бюджеты / Лимиты`
- `Настройки -> Бюджеты / Лимиты`

Current weekly and monthly budgets are shown as clickable cards. Editing an
amount now creates a pending confirmation first; the value is saved only after
the user taps `Сохранить`. Deleting a budget requires confirmation and removes
only the budget value. Financial operations, categories, reminders, and
workspace data are not deleted.

## Category Creation

Users can open `Настройки -> Категории`, choose expense or income category type,
and enter a category name. Names are normalized through the existing category
service. Empty names are rejected. Existing active duplicates in the same
workspace/type are rejected by returning the user to the category flow.

The implementation uses `custom_categories`; it does not introduce a second
category system.

## Category Transfer and Deletion

`Перенести записи` moves all records from a source category to a destination
category inside one database transaction. The transfer keeps amounts, dates,
currencies, descriptions, sources, authors, and workspace scope unchanged.

The transaction updates known category references:

- `operations`
- `operation_drafts`
- `category_limits`
- `category_budget_group_members`
- `user_reminders`
- `user_aliases`
- `ml_observations.chosen_category`

Deleting a category with historical operations first requires selecting a
destination. The source records are transferred, then the custom source category
is archived with `archived_at` where possible. Deleting an empty category still
requires confirmation and clears category-specific budgets/references before
archiving.

System fallback categories such as `Без операций` are protected from deletion.

## Evening Tips

Evening reminders may include one short rotating feature tip and one CTA button.
Tips are added only to the existing evening reminder message and respect the
existing evening-reminder preference. No separate tip notification is sent. Mini
App links are not shown.

## Report Export Buttons

Weekly and monthly report jobs add exact-period XLSX export buttons:

- `Экспорт за неделю`
- `Экспорт за месяц`

The callback carries the exact report period and reuses the existing XLSX export
builder.

## Migration

No migration is required for this release. Existing `custom_categories.archived_at`
supports soft deletion, and the existing reminder log supports predictable tip
rotation without new tables.

Production migration command: not required.

Rollback procedure:

1. Revert the application commit.
2. Redeploy the previous application version.
3. No database rollback is needed because no schema migration is added.
