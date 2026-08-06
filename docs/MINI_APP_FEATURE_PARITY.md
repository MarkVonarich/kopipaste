# Mini App Feature Parity

This inventory is based on the current repository code in `routers/`, `services/`, and `miniapp/`. It is for planning follow-up PRs and does not define new endpoints.

| Функция | Есть в Telegram Bot | Есть в Mini App | Текущий backend service/API | Текущий статус | Целевой следующий PR | Комментарий |
|---|---:|---:|---|---|---|---|
| Ручное добавление операций | Да | Да | `routers.messages`, `services.operations.record_financial_operation`; `POST /miniapp/api/operations` | Parity | UX polish | Mini App uses category picker and idempotent create. |
| Natural-language текстовый ввод | Да | Нет | `routers.messages.handle_text`, operation parsing helpers | Bot only | Mini App NL input | No Mini App endpoint for free-form parsing. |
| Голосовой ввод | Да | Нет | `routers.messages.handle_voice`, `services.voice_transcription` | Bot only | Mini App voice capture/upload | Current voice pipeline depends on Telegram bot media. |
| OCR чеков | Да | Нет | `routers.messages` receipt flow, `services.receipt_parser` | Bot only | Mini App receipt upload | No Mini App receipt endpoint or review UI. |
| Операции: список, детали, редактирование, удаление | Да | Да | `services.operations`; `GET/PATCH/DELETE /miniapp/api/operations/:id` | Parity | Bulk/action polish | Workspace access stays server-side. |
| Категории: выбор | Да | Да | `services.categories`; `GET /miniapp/api/categories`, `GET /miniapp/api/profile/categories` | Parity for selection | Category management PR | Mini App can list/select managed categories. |
| Категории: создание custom during flow | Да | Нет | `services.categories.get_or_create_custom_category`; bot callbacks/messages | Bot only | Category lifecycle PR | Mini App currently requires existing category option. |
| Перенос категорий | Да | Нет | `services.categories.transfer_category`, bot callbacks | Bot only | Category lifecycle PR | No Mini App management UI/API. |
| Объединение категорий | Да | Нет | `services.categories.rename_category`, duplicate/transfer flows | Bot only | Category lifecycle PR | No Mini App merge/duplicate resolution flow. |
| Бюджеты | Да | Частично | `db.queries.get_user_budgets`, `services.budgeting`; Mini App Plans limits | Partial | Budget parity PR | Mini App focuses on general/category limits, not every legacy budget screen. |
| Общие лимиты | Да | Да | `services.budgeting`, `services.miniapp_limits`; `POST/PATCH/DELETE /miniapp/api/limits` with `all_expenses` | Parity | Threshold UX polish | Mini App uses existing limit service and idempotency. |
| Лимиты категорий | Да | Да | `services.miniapp_limits`, `services.limit_alerts`; `/miniapp/api/limits` | Parity | Category management PR | Usage/status returned by API. |
| Цели | Да | Да | `services.goals`; `/miniapp/api/goals`, plan preview, contributions, reminders, status | Parity | Goal detail/history PR | Mini App supports preview-before-save and idempotent creates. |
| Челленджи | Да | Нет | `services.challenges`, bot callbacks | Bot only | Mini App challenges PR | Current Mini App API has no challenge endpoints. |
| Достижения | Да | Нет | `services.achievements`, challenge/notification flows | Bot only | Mini App challenges PR | No Mini App achievement screen/API. |
| Напоминания | Да | Частично | `routers.commands.cmd_reminders`, `user_reminders`; Mini App notification preferences | Partial | Reminders PR | Mini App can toggle notifications/quiet hours but not manage reminders. |
| Регулярные платежи | Да | Нет | `user_reminders`, `services.reminder_totals`, recurring notification facts | Bot only | Reminders PR | No Mini App recurring payment CRUD. |
| Недельные отчёты | Да | Нет | `jobs.daily.build_weekly_report_text`, admin/report callbacks | Bot only | Reports PR | Mini App has analytics, not report generation. |
| Месячные отчёты | Да | Нет | `jobs.daily.build_monthly_report_text`, report callbacks | Bot only | Reports PR | Mini App has analytics, not monthly report delivery. |
| Excel export | Да | Частично | `services.export_xlsx`; `GET/POST /miniapp/api/profile/export` | Partial | Export PR | Mini App exposes export info/entry to existing Telegram flow, not file download. |
| Валюта | Да | Частично | `db.queries.get_user_currency`; profile/bootstrap | Partial | Profile settings PR | Mini App displays currency and uses it as fallback; no edit UI. |
| Часовой пояс | Да | Частично | `services.user_time`, `services.notification_preferences`; Mini App notification update action `timezone` | Partial | Profile settings PR | API can update timezone, current UI primarily displays it. |
| Quiet hours | Да | Да | `services.notification_preferences`, `services.automatic_notifications`; `POST /miniapp/api/profile/notifications` | Parity for toggle | Quiet-hours edit UI | Mini App toggles quiet hours; detailed time editing can be expanded. |
| Пространства | Да | Да | `services.workspaces`; `GET /miniapp/api/workspaces`, bootstrap workspace state | Parity | Workspace management PR | Mini App switches existing accessible spaces. |
| Уведомления | Да | Да | `services.notification_preferences`; `GET/POST /miniapp/api/profile/notifications` | Parity for preferences | Notification details PR | Feature-specific toggles are present. |
| Аналитика | Частично | Да | `services.analytics` concepts; `/miniapp/api/analytics` | Mini App lead | Analytics PRs | Mini App owns charts, radar, and multicurrency structure. |
| Экспорт персональных данных | Да | Частично | privacy/export bot flow; `profile/export` info | Partial | Privacy/data PR | Mini App does not generate personal export file directly. |
| Удаление данных | Да | Нет | `services.analytics_privacy.apply_account_deletion`, `services.personal_data_deletion` | Bot only | Privacy/data PR | No Mini App deletion confirmation flow. |
| Помощь | Да | Да | `ui.keyboards.help_menu_kb`; `MINIAPP_HELP_URL` in profile | Parity | Content polish | Mini App links to configured help URL. |
| Поддержка | Да | Да | support username/help URL settings | Parity | Support PR | Mini App additional menu opens configured support/help. |
| Privacy | Да | Да | `MINIAPP_PRIVACY_URL`, bot privacy menu | Parity for link | Privacy/data PR | Mini App shows configured legal link when present. |
| Terms | Да | Да | `MINIAPP_TERMS_URL` | Parity for link | Legal content PR | Mini App shows configured legal link when present. |
| Premium information screen | Да | Да | `MiniAppAPI._premium_info`, bot menus where present | Parity for info-only | Premium product PR | No payments or entitlements in MVP. |
