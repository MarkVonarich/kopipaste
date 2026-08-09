# Mini App Feature Parity

This inventory is based on the current repository code in `routers/`, `services/`, and `miniapp/`. It is for planning follow-up PRs and does not define new endpoints.

| Функция | Есть в Telegram Bot | Есть в Mini App | Текущий backend service/API | Текущий статус | Целевой следующий PR | Комментарий |
|---|---:|---:|---|---|---|---|
| Ручное добавление операций | Да | Да | `routers.messages`, `services.operations.record_financial_operation`; `POST /miniapp/api/operations` | Parity | UX polish | Mini App uses category picker and idempotent create. |
| Natural-language текстовый ввод | Да | Нет | `routers.messages.handle_text`, operation parsing helpers | Bot only | Mini App NL input | No Mini App endpoint for free-form parsing. |
| Голосовой ввод | Да | Нет | `routers.messages.handle_voice`, `services.voice_transcription` | Bot only | Mini App voice capture/upload | Current voice pipeline depends on Telegram bot media. |
| OCR чеков | Да | Нет | `routers.messages` receipt flow, `services.receipt_parser` | Bot only | Mini App receipt upload | No Mini App receipt endpoint or review UI. |
| Операции: список, детали, редактирование, удаление | Да | Да | `services.operations`; `GET/PATCH/DELETE /miniapp/api/operations/:id` | Parity | Bulk/action polish | Workspace access stays server-side. |
| Категории: выбор | Да | Да | `services.categories`; `GET /miniapp/api/categories`, `GET /miniapp/api/categories/manage` | Parity | UX polish | Mini App can list/select and manage categories from Plans. |
| Категории: создание custom during flow | Да | Частично | `services.categories.get_or_create_custom_category`; Mini App category management API | Management parity | Operation-flow polish | Mini App creates categories from Plans, not inline while adding an operation. |
| Перенос категорий | Да | Да | `services.categories.transfer_category`; `DELETE /miniapp/api/categories/{token}` | Parity | UX polish | Delete with references requires a transfer target. |
| Объединение категорий | Да | Да | `services.categories.rename_category`, duplicate/transfer flows | Parity | UX polish | Rename into an existing normalized destination uses the shared category service. |
| Бюджеты | Да | Да | `services.budgeting`; Mini App Plans limits/category budgets | Parity for general and category-group budgets | Threshold UX polish | Mini App reuses `general_spending_limits` and `category_budget_groups`, not a parallel budget system. |
| Общие лимиты | Да | Да | `services.budgeting`, `services.miniapp_limits`; `POST/PATCH/DELETE /miniapp/api/limits` with `all_expenses` | Parity | Threshold UX polish | Mini App uses existing limit service and idempotency. |
| Лимиты категорий | Да | Да | `services.miniapp_limits`, `services.limit_alerts`; `/miniapp/api/limits` | Parity | Category management PR | Usage/status returned by API. |
| Цели | Да | Да | `services.goals`; `/miniapp/api/goals`, plan preview, contributions, reminders, status | Parity | Goal detail/history PR | Mini App supports preview-before-save and idempotent creates. |
| Челленджи | Legacy only | Да | `services.challenges`, `GET /miniapp/api/overview` | Mini App lead | Mini App challenges PR | Telegram challenge menu now routes users to Mini App; Telegram challenge notifications are retired. |
| Достижения | Да | Нет | `services.achievements`, challenge/notification flows | Bot only | Mini App challenges PR | No Mini App achievement screen/API. |
| Напоминания | Да | Да | `services.reminders`, `user_reminders`, `user_reminder_events`; `/miniapp/api/reminders` | Parity | Polish/history PR | Mini App and bot share the same reminders table and record/snooze semantics. |
| Регулярные платежи | Да | Да | `services.reminders`, recurring `repeat_rule` fields | Parity | Polish/history PR | Recording a recurring reminder advances the existing reminder to the next event date. |
| Недельные отчёты | Да | Нет | `jobs.daily.build_weekly_report_text`, admin/report callbacks | Bot only | Reports PR | Mini App has analytics, not report generation. |
| Месячные отчёты | Да | Нет | `jobs.daily.build_monthly_report_text`, report callbacks | Bot only | Reports PR | Mini App has analytics, not monthly report delivery. |
| Excel export | Да | Да | `services.export_xlsx`; `GET/POST /miniapp/api/profile/export` | Parity | Export polish | Mini App exposes export from Analytics, previews a period and sends XLSX to the Telegram chat. |
| Валюта | Да | Да | `db.queries.get_user_currency`; `POST /miniapp/api/profile/currency` | Parity | Currency polish | Mini App edits future default currency only; operation history is unchanged. |
| Часовой пояс | Да | Да | `services.user_time`, `services.notification_preferences`; `POST /miniapp/api/profile/timezone` | Parity | Timezone polish | Mini App exposes bot timezone presets plus custom IANA input. |
| Quiet hours | Да | Да | `services.notification_preferences`, `services.automatic_notifications`; `POST /miniapp/api/profile/notifications` | Parity | Notification polish | Mini App saves enabled/start/end atomically through the editor. |
| Пространства | Да | Да | `services.workspaces`; `GET /miniapp/api/workspaces`, `POST /miniapp/api/profile/active-workspace`, `PATCH /miniapp/api/workspaces/:id` | Parity | Workspace polish | Mini App switches active workspace and lets owner/admin rename accessible workspaces. |
| Уведомления | Да | Да | `services.notification_preferences`; `GET/POST /miniapp/api/profile/notifications` | Parity | Notification polish | Mini App and bot expose evening Daily, Plans/Control, Reports and Quiet Hours controls. Morning automatic nudges are retired. |
| Аналитика | Частично | Да | `services.analytics` concepts; `/miniapp/api/analytics` | Mini App lead | Analytics PRs | Mini App owns charts, radar, and multicurrency structure. |
| Экспорт персональных данных | Да | Частично | privacy/export bot flow; `profile/export` info | Partial | Privacy/data PR | Mini App does not generate personal export file directly. |
| Удаление данных | Да | Нет | `services.analytics_privacy.apply_account_deletion`, `services.personal_data_deletion` | Bot only | Privacy/data PR | No Mini App deletion confirmation flow. |
| Помощь | Да | Да | `ui.keyboards.help_menu_kb`; `MINIAPP_HELP_URL` in profile | Parity | Content polish | Mini App links to configured help URL. |
| Поддержка | Да | Да | support username/help URL settings | Parity | Support PR | Mini App additional menu opens configured support/help. |
| Privacy | Да | Да | `MINIAPP_PRIVACY_URL`, bot privacy menu | Parity for link | Privacy/data PR | Mini App shows configured legal link when present. |
| Terms | Да | Да | `MINIAPP_TERMS_URL` | Parity for link | Legal content PR | Mini App shows configured legal link when present. |
| Premium information screen | Да | Да | `MiniAppAPI._premium_info`, bot menus where present | Parity for info-only | Premium product PR | No payments or entitlements in MVP. |

## Current Mini App Home/Profile Additions

- Home now shows at most three recent operations, equal Challenge/Focus/Reminder smart-card carousels, and a full-width rule-based period insight.
- Home challenge carousel shows at most one current day, week and month card in that order.
- Home insight never adds mixed currencies together and falls back safely when previous-period data is unavailable.
- Global financial filters now cover period, current week, operation type and category across Home, Operations and Analytics.
- Analytics Radar compares absolute category amounts in one selected currency with adaptive money ticks and full category labels.
- Home includes the activity calendar based on operation counts, not amounts, plus current streak and active-days summary for the selected global filter slice.
- Analytics keeps Dynamics/Category Structure details collapsed by default and no longer displays the Activity calendar.
- Home focus uses risk severity for limits and goals and renders the same progress-bar language used by Plans.
- Home includes reminder cards from the existing bot reminders; tapping one opens actions and record creates a real financial operation.
- Plans now has `Цели`, `Лимиты и бюджеты`, `Напоминания`, and `Категории`; reminders support create/edit/toggle/snooze/delete/record through the existing `user_reminders` domain.
- Plans separates Общие лимиты, Бюджеты категорий and Лимиты категорий. General limits use `general_spending_limits`; category budgets use `category_budget_groups`.
- Telegram launch supports the persistent menu button label `Открыть`, `/app`, and manual BotFather Main Mini App setup.
- Profile uses accordion sections for User, Appearance, Workspaces, Notifications, Premium, Help and Legal; tapping an open section closes it.
- Analytics owns the Mini App export entry. Custom export preset shows date fields immediately and preserves chosen dates.
- The global three-dot menu is available from every Mini App tab and uses Telegram native Add to Home Screen when supported.
- Preferred name is shared with bot confirmations through `public.users.preferred_name`.
- Currency, timezone, active workspace, workspace rename and quiet-hours editor are available from Profile without changing historical operations.
