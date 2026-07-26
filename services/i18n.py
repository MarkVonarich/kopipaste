from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

SUPPORTED_LOCALES = {"ru", "en"}
DEFAULT_LOCALE = "en"

TRANSLATIONS = {
    "ru": {
        "workspace.personal": "Личное пространство",
        "notifications.inactivity.reason": "Сегодня еще не было успешных записей.",
        "privacy.title": "🔐 Конфиденциальность",
        "privacy.body": "Управление экспортом, финансовой историей и удалением аккаунта.",
        "privacy.export_data": "📤 Экспортировать данные",
        "privacy.clear_history": "🧹 Очистить финансовую историю",
        "privacy.delete_account": "🗑 Удалить аккаунт и все данные",
        "privacy.back": "⬅️ Назад",
        "privacy.history.title": "🧹 Очистить финансовую историю",
        "privacy.history.body": "Выберите период. Аккаунт, категории, настройки, лимиты и бюджеты сохранятся.",
        "privacy.period.today": "Сегодня",
        "privacy.period.last7": "Последние 7 дней",
        "privacy.period.this_month": "Этот месяц",
        "privacy.period.prev_month": "Прошлый месяц",
        "privacy.period.this_year": "Этот год",
        "privacy.period.all": "Всё время",
        "privacy.period.custom": "📅 Свой период",
        "privacy.history.preview": "Удалить финансовую историю?\n\nПериод: {period}\nБудет удалено операций: {count}\n\nКатегории, настройки, лимиты и аккаунт сохранятся.\n\nЭто действие нельзя отменить.",
        "privacy.history.zero": "За выбранный период нет операций для удаления.\n\nПериод: {period}",
        "privacy.history.yes": "🗑 Да, удалить",
        "privacy.history.no": "Нет, оставить",
        "privacy.history.success": "✅ Готово\n\nУдалено операций: {count}\nПериод: {period}\n\nВаш аккаунт, категории и настройки сохранены.",
        "privacy.history.failed": "Не удалось безопасно удалить историю. Попробуйте ещё раз позже.",
        "privacy.history.main": "🏠 Главное меню",
        "privacy.history.another": "🧹 Очистить другой период",
        "privacy.custom.start": "Введите дату начала (DD.MM.YYYY, DD.MM или YYYY-MM-DD):",
        "privacy.custom.end": "Введите дату конца (DD.MM.YYYY, DD.MM или YYYY-MM-DD):",
        "privacy.custom.invalid": "Не понял дату. Попробуйте ещё раз.",
        "privacy.custom.end_before_start": "Дата конца не может быть раньше даты начала.",
        "privacy.cancelled": "Действие отменено.",
        "privacy.stale": "Подтверждение устарело. Начните заново.",
        "privacy.delete.explain": "🗑 Удалить аккаунт и все данные\n\nБудут удалены персональные операции, напоминания, лимиты, бюджеты, настройки и профиль.\n\nПолитика общих групп: участие удаляется; общая история может быть сохранена и анонимизирована, если она нужна другим участникам.",
        "privacy.delete.continue": "🗑 Продолжить удаление",
        "privacy.delete.confirm": "Действительно ли вы хотите удалить все свои данные?\n\nБудут удалены операции, напоминания, лимиты, бюджеты,\nнастройки и персональный профиль.\n\nЭто действие нельзя отменить.",
        "privacy.delete.yes": "🗑 Да, удалить",
        "privacy.delete.no": "Нет, оставить",
        "privacy.delete.success": "✅ Ваши данные удалены\n\nПерсональные операции, настройки и профиль удалены.\nПри следующем /start бот начнёт работу как с новым пользователем.",
        "privacy.delete.failed": "Не удалось безопасно удалить данные. Попробуйте ещё раз позже.",
        "help.commands": "Команды меню: /start, /settings, /help.",
        "reminders.recurring_per_month": "Повторяющиеся в месяц",
        "reminders.one_time_upcoming": "Разовые предстоящие",
        "reminders.expenses": "Расходы",
        "reminders.income": "Доходы",
        "menu.main.title": "🔷 Главное меню:",
        "menu.add_operation": "➕ Добавить операцию",
        "menu.limits_budgets": "💰 Лимиты и бюджеты",
        "menu.reminders": "🔔 Напоминания",
        "menu.export": "📤 Экспорт",
        "menu.settings": "⚙️ Настройки",
        "menu.help": "❓ Помощь",
        "menu.back": "⬅️ Назад",
        "limits_budgets.title": "💰 Лимиты и бюджеты",
        "limits_budgets.category_limits": "📂 Лимиты категорий",
        "limits_budgets.general_limit": "📊 Общий лимит",
        "limits_budgets.category_budget": "🧩 Бюджет из категорий",
        "limits_budgets.spending_status": "📈 Статус расходов",
        "limits_budgets.notifications": "⚙️ Оповещения",
        "notifications.title": "🔔 Оповещения",
        "notifications.morning_on": "✅ Утро: включено",
        "notifications.morning_off": "⛔ Утро: выключено",
        "notifications.evening_on": "✅ Вечер: включено",
        "notifications.evening_off": "⛔ Вечер: выключено",
    },
    "en": {
        "workspace.personal": "Personal space",
        "notifications.inactivity.reason": "No successful records today yet.",
        "privacy.title": "🔐 Privacy",
        "privacy.body": "Manage export, financial history, and account deletion.",
        "privacy.export_data": "📤 Export data",
        "privacy.clear_history": "🧹 Clear financial history",
        "privacy.delete_account": "🗑 Delete account and all data",
        "privacy.back": "⬅️ Back",
        "privacy.history.title": "🧹 Clear financial history",
        "privacy.history.body": "Choose a period. Your account, categories, settings, limits, and budgets will be kept.",
        "privacy.period.today": "Today",
        "privacy.period.last7": "Last 7 days",
        "privacy.period.this_month": "This month",
        "privacy.period.prev_month": "Previous month",
        "privacy.period.this_year": "This year",
        "privacy.period.all": "All time",
        "privacy.period.custom": "📅 Custom period",
        "privacy.history.preview": "Delete financial history?\n\nPeriod: {period}\nOperations to delete: {count}\n\nCategories, settings, limits, and account access will be kept.\n\nThis action cannot be undone.",
        "privacy.history.zero": "There are no operations to delete for this period.\n\nPeriod: {period}",
        "privacy.history.yes": "🗑 Yes, delete",
        "privacy.history.no": "No, keep it",
        "privacy.history.success": "✅ Done\n\nDeleted operations: {count}\nPeriod: {period}\n\nYour account, categories, and settings were kept.",
        "privacy.history.failed": "Could not safely delete history. Please try again later.",
        "privacy.history.main": "🏠 Main menu",
        "privacy.history.another": "🧹 Clear another period",
        "privacy.custom.start": "Enter start date (DD.MM.YYYY, DD.MM, or YYYY-MM-DD):",
        "privacy.custom.end": "Enter end date (DD.MM.YYYY, DD.MM, or YYYY-MM-DD):",
        "privacy.custom.invalid": "I could not understand the date. Please try again.",
        "privacy.custom.end_before_start": "End date cannot be before start date.",
        "privacy.cancelled": "Action cancelled.",
        "privacy.stale": "Confirmation expired. Please start again.",
        "privacy.delete.explain": "🗑 Delete account and all data\n\nPersonal operations, reminders, limits, budgets, settings, and profile will be deleted.\n\nShared group workspace policy: membership is removed; shared ledger operations may be retained and anonymized if needed for other participants.",
        "privacy.delete.continue": "🗑 Continue deletion",
        "privacy.delete.confirm": "Do you really want to delete all your data?\n\nOperations, reminders, limits, budgets,\nsettings, and personal profile will be deleted.\n\nThis action cannot be undone.",
        "privacy.delete.yes": "🗑 Yes, delete",
        "privacy.delete.no": "No, keep it",
        "privacy.delete.success": "✅ Your data was deleted\n\nPersonal operations, settings, and profile were deleted.\nOn the next /start, the bot will start as with a new user.",
        "privacy.delete.failed": "Could not safely delete data. Please try again later.",
        "help.commands": "Menu commands: /start, /settings, /help.",
        "reminders.recurring_per_month": "Recurring per month",
        "reminders.one_time_upcoming": "One-time upcoming",
        "reminders.expenses": "Expenses",
        "reminders.income": "Income",
        "menu.main.title": "🔷 Main menu:",
        "menu.add_operation": "➕ Add operation",
        "menu.limits_budgets": "💰 Limits and budgets",
        "menu.reminders": "🔔 Reminders",
        "menu.export": "📤 Export",
        "menu.settings": "⚙️ Settings",
        "menu.help": "❓ Help",
        "menu.back": "⬅️ Back",
        "limits_budgets.title": "💰 Limits and budgets",
        "limits_budgets.category_limits": "📂 Category limits",
        "limits_budgets.general_limit": "📊 General limit",
        "limits_budgets.category_budget": "🧩 Budget from categories",
        "limits_budgets.spending_status": "📈 Spending status",
        "limits_budgets.notifications": "⚙️ Notifications",
        "notifications.title": "🔔 Notifications",
        "notifications.morning_on": "✅ Morning: on",
        "notifications.morning_off": "⛔ Morning: off",
        "notifications.evening_on": "✅ Evening: on",
        "notifications.evening_off": "⛔ Evening: off",
    },
}


def normalize_locale(locale: str | None) -> str:
    value = (locale or DEFAULT_LOCALE).replace("-", "_").split("_", 1)[0].lower()
    return value if value in SUPPORTED_LOCALES else DEFAULT_LOCALE


def resolve_locale(saved_locale: str | None = None, telegram_language_code: str | None = None) -> str:
    if saved_locale:
        return normalize_locale(saved_locale)
    return normalize_locale(telegram_language_code)


def t(key: str, locale: str | None = None, **kwargs) -> str:
    lang = normalize_locale(locale)
    template = TRANSLATIONS.get(lang, {}).get(key) or TRANSLATIONS[DEFAULT_LOCALE].get(key) or key
    return template.format(**kwargs) if kwargs else template


def format_money(amount, currency: str = "RUB", locale: str | None = None) -> str:
    lang = normalize_locale(locale)
    value = Decimal(str(amount or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if value == value.to_integral():
        raw = f"{int(value):,}"
    else:
        raw = f"{value:,.2f}"
    if lang == "ru":
        raw = raw.replace(",", " ").replace(".", ",")
    return f"{raw} {currency}"
