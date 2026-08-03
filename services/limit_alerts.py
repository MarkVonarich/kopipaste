from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_FLOOR
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


HALF_USED_BAND = 50
APPROACHING_BANDS = (50, 80, 90)
REACHED_BAND = 100
EXCEEDED_BAND = 101


@dataclass(frozen=True)
class LimitAlert:
    status: str
    threshold_band: int
    percentage: int
    text: str
    parse_mode: str = "HTML"


def _money(value: Decimal | int | float | str, currency: str | None = None) -> str:
    amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    if amount == amount.to_integral_value():
        rendered = f"{int(amount):,}".replace(",", " ")
    else:
        rendered = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    return f"{rendered} {html.escape(currency or 'RUB')}"


def threshold_band(spent: Decimal | int | float | str, limit: Decimal | int | float | str) -> int | None:
    limit_dec = Decimal(str(limit or 0))
    if limit_dec <= 0:
        return None
    spent_dec = Decimal(str(spent or 0))
    percentage = int(((spent_dec * Decimal("100")) / limit_dec).to_integral_value(rounding=ROUND_FLOOR))
    if spent_dec > limit_dec:
        return EXCEEDED_BAND
    if percentage == 100:
        return REACHED_BAND
    if percentage >= 90:
        return 90
    if percentage >= 80:
        return 80
    if percentage >= 50:
        return 50
    return None


def alert_status_for_band(band: int) -> str:
    if band == EXCEEDED_BAND:
        return "exceeded"
    if band == REACHED_BAND:
        return "reached"
    if band == HALF_USED_BAND:
        return "half_used"
    return "approaching"


def build_category_limit_dedupe_key(
    *,
    user_id: int,
    workspace_id: int | None,
    period: str,
    period_start: date,
    category_key: str,
    band: int,
) -> str:
    workspace_part = workspace_id if workspace_id is not None else 0
    return f"category_limit:{user_id}:{workspace_part}:{period}:{period_start.isoformat()}:{category_key}:{band}"


def build_category_limit_exceeded_dedupe_key(
    *,
    user_id: int,
    workspace_id: int | None,
    period: str,
    period_start: date,
    category_key: str,
    operation_id: int,
) -> str:
    workspace_part = workspace_id if workspace_id is not None else 0
    return f"category_limit_exceeded:{user_id}:{workspace_part}:{period}:{period_start.isoformat()}:{category_key}:{int(operation_id)}"


def render_category_limit_alert(
    *,
    category: str,
    period: str,
    spent: Decimal | int | float | str,
    limit: Decimal | int | float | str,
    currency: str | None,
    intensified: bool = False,
) -> LimitAlert | None:
    limit_dec = Decimal(str(limit or 0))
    if limit_dec <= 0:
        return None
    spent_dec = Decimal(str(spent or 0))
    percentage = int(((spent_dec * Decimal("100")) / limit_dec).to_integral_value(rounding=ROUND_FLOOR))
    band = threshold_band(spent_dec, limit_dec)
    if band is None:
        return None
    status = alert_status_for_band(band)
    period_label = "недельный" if period == "week" else "месячный"
    category_html = html.escape(category or "Категория")
    spent_txt = _money(spent_dec, currency)
    limit_txt = _money(limit_dec, currency)
    if status == "exceeded":
        exceeded_txt = _money(spent_dec - limit_dec, currency)
        title = "🚨 Лимит превышен ещё сильнее" if intensified else "🚨 Лимит превышен"
        detail = f"Превышение: <b>{exceeded_txt}</b>\n\nОбрати внимание на следующие траты по этой категории."
    elif status == "reached":
        title = "🚨 Лимит исчерпан"
        detail = "Следующая трата превысит лимит."
    elif status == "half_used":
        remaining_txt = _money(limit_dec - spent_dec, currency)
        title = "ℹ️ Половина лимита использована"
        detail = f"Осталось: <b>{remaining_txt}</b>\n\nПока всё в пределах плана."
    else:
        remaining_txt = _money(limit_dec - spent_dec, currency)
        title = "⚠️ До лимита осталось совсем немного" if band == 90 else "⚠️ Лимит почти израсходован"
        tail = "\n\nБудь внимательнее с дальнейшими тратами." if band == 80 else ""
        detail = f"Осталось: <b>{remaining_txt}</b>{tail}"
    text = (
        f"{title}\n\n"
        f"<b>{category_html}</b> — {period_label} лимит.\n"
        f"Использовано: <b>{percentage}%</b>\n"
        f"Потрачено: <b>{spent_txt}</b> из <b>{limit_txt}</b>\n"
        f"{detail}"
    )
    return LimitAlert(status=status, threshold_band=band, percentage=percentage, text=text)


def category_limit_alert_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Открыть лимиты", callback_data="lim_list")],
            [InlineKeyboardButton("Изменить лимит", callback_data="lim_list")],
            [InlineKeyboardButton("Отключить уведомления", callback_data="menu_notifications")],
        ]
    )


def safe_limit_threshold_event_properties(*, band: int, period: str, status: str, currency: str | None, source: str) -> dict[str, Any]:
    return {
        "threshold_band": "exceeded" if band == EXCEEDED_BAND else int(band),
        "period": period,
        "status": status,
        "currency": currency or "RUB",
        "source": source,
    }
