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
