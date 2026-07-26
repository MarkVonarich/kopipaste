from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

SUPPORTED_LOCALES = {"ru", "en"}
DEFAULT_LOCALE = "ru"

TRANSLATIONS = {
    "ru": {
        "workspace.personal": "Личное пространство",
        "notifications.inactivity.reason": "Сегодня еще не было успешных записей.",
        "privacy.title": "Приватность",
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
        "privacy.title": "Privacy",
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
    value = (locale or DEFAULT_LOCALE).split("_", 1)[0].lower()
    return value if value in SUPPORTED_LOCALES else DEFAULT_LOCALE


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
