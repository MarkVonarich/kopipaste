from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import Any, Iterable

from utils.money import to_decimal_money

EMPTY_MERCHANT_KEY = "__empty_merchant__"
SYNTHETIC_OTHER_MERCHANT_KEY = "__synthetic_other_merchant__"
BASELINE_MIN_PERIODS = 3
BASELINE_MIN_OPERATIONS = 3

_WHITESPACE_RE = re.compile(r"\s+")
_UNSUPPORTED_KEY_CHARS_RE = re.compile(r"[^0-9a-zа-я]+")
_UNSUPPORTED_DISPLAY_CHARS_RE = re.compile(r"[^0-9A-Za-zА-Яа-яЁё]+")
_UPPER_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
_LOWER_CHARS = "abcdefghijklmnopqrstuvwxyzабвгдеёжзийклмнопрстуфхцчшщъыьэюя"
_PY_TRANSLATE = str.maketrans(_UPPER_CHARS, _LOWER_CHARS)


@dataclass(frozen=True)
class MerchantIdentity:
    key: str
    display_name: str
    source: str
    raw_aliases: tuple[str, ...]
    drillable: bool = True
    fallback: bool = False


def normalize_merchant_key(value: str | None) -> str:
    """Return the conservative Merchant Key V1 that mirrors merchant_key_sql()."""
    raw = (value or "").strip()
    text = raw.translate(_PY_TRANSLATE).replace("ё", "е")
    key = _WHITESPACE_RE.sub(" ", _UNSUPPORTED_KEY_CHARS_RE.sub(" ", text)).strip()[:120]
    return key or raw[:120]


def clean_merchant_display(value: str | None) -> str:
    text = (value or "").strip()
    return _WHITESPACE_RE.sub(" ", _UNSUPPORTED_DISPLAY_CHARS_RE.sub(" ", text)).strip()[:120]


def _has_cased_letters(text: str) -> bool:
    return any(char.isalpha() and char.lower() != char.upper() for char in text)


def _display_candidate(raw_value: str) -> str:
    cleaned = clean_merchant_display(raw_value)
    if _has_cased_letters(cleaned) and cleaned == cleaned.upper():
        return cleaned.title()
    return cleaned


def _display_sort_key(item: tuple[str, dict[str, Any]]) -> tuple[Any, ...]:
    raw, stats = item
    display = _display_candidate(raw)
    original = (raw or "").strip()
    punctuation_penalty = 1 if clean_merchant_display(original) != original else 0
    uppercase_penalty = 1 if _has_cased_letters(original) and original == original.upper() else 0
    return (
        punctuation_penalty + uppercase_penalty,
        -int(stats.get("count") or 0),
        -to_decimal_money(stats.get("total") or 0),
        len(display),
        display.lower(),
    )


def choose_display_name(raw_values: dict[str, dict[str, Any]], key: str) -> str:
    if not raw_values:
        return key
    raw, _stats = sorted(raw_values.items(), key=_display_sort_key)[0]
    display = _display_candidate(raw)
    return display or key


def merchant_identity(raw_value: str | None) -> MerchantIdentity:
    raw = (raw_value or "").strip()
    key = normalize_merchant_key(raw)
    if not key:
        return MerchantIdentity(EMPTY_MERCHANT_KEY, "Без описания", "fallback_empty", (), drillable=False, fallback=True)
    display = _display_candidate(raw) or key
    return MerchantIdentity(key, display, "deterministic", (raw,), drillable=True, fallback=False)


def merchant_key_sql(column: str) -> str:
    normalized = (
        "LEFT(TRIM(REGEXP_REPLACE("
        f"REGEXP_REPLACE(REPLACE(TRANSLATE(COALESCE({column},''), '{_UPPER_CHARS}', '{_LOWER_CHARS}'), 'ё', 'е'), "
        "'[^0-9a-zа-я]+', ' ', 'g'), '\\s+', ' ', 'g')), 120)"
    )
    return (
        f"COALESCE(NULLIF({normalized}, ''), LEFT(TRIM(COALESCE({column},'')), 120), '')"
    )


def fold_merchant_rows(rows: Iterable[tuple[str | None, str, Decimal, int]]) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for raw_name, currency, total, count in rows:
        currency_code = str(currency)
        raw = (raw_name or "").strip()
        identity = merchant_identity(raw)
        bucket = grouped.setdefault(currency_code, {}).setdefault(
            identity.key,
            {
                "key": identity.key,
                "name": identity.display_name,
                "total": Decimal("0.00"),
                "count": 0,
                "synthetic": False,
                "drillable": identity.drillable,
                "fallback": identity.fallback,
                "source": identity.source,
                "raw_values": {},
            },
        )
        amount = to_decimal_money(total)
        bucket["total"] += amount
        bucket["count"] += int(count or 0)
        if raw:
            raw_bucket = bucket["raw_values"].setdefault(raw, {"count": 0, "total": Decimal("0.00")})
            raw_bucket["count"] += int(count or 0)
            raw_bucket["total"] += amount
        bucket["name"] = choose_display_name(bucket["raw_values"], identity.key) if bucket["raw_values"] else identity.display_name
        bucket["drillable"] = bool(bucket["drillable"] and identity.drillable)
        bucket["fallback"] = bool(bucket["fallback"] or identity.fallback)
    return grouped


def raw_aliases_for_bucket(bucket: dict[str, Any], *, limit: int = 6) -> list[str]:
    raw_values = bucket.get("raw_values") or {}
    aliases = sorted(
        raw_values.items(),
        key=lambda item: (-int(item[1].get("count") or 0), -to_decimal_money(item[1].get("total") or 0), item[0].lower()),
    )
    return [raw.strip() for raw, _stats in aliases[:limit] if raw.strip()]


def _last_day_of_month(year: int, month: int) -> int:
    next_month = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
    return (next_month - timedelta(days=1)).day


def _shift_month(dt: date, months_back: int) -> date:
    month_index = dt.year * 12 + (dt.month - 1) - months_back
    year = month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(dt.day, _last_day_of_month(year, month)))


def _is_full_month(start: date, end: date) -> bool:
    return start.day == 1 and end.day == _last_day_of_month(end.year, end.month) and start.year == end.year and start.month == end.month


def comparable_baseline_periods(start: date, end: date, period_key: str, *, count: int = BASELINE_MIN_PERIODS) -> list[tuple[date, date]]:
    if count <= 0:
        return []
    if _is_full_month(start, end) or period_key == "previous_month":
        periods = []
        anchor = start.replace(day=1)
        for index in range(1, count + 1):
            shifted = _shift_month(anchor, index).replace(day=1)
            periods.append((shifted, shifted.replace(day=_last_day_of_month(shifted.year, shifted.month))))
        return periods
    if period_key == "current_month" and start.day == 1:
        periods = []
        for index in range(1, count + 1):
            shifted_start = _shift_month(start, index)
            last_day = _last_day_of_month(shifted_start.year, shifted_start.month)
            shifted_end = shifted_start.replace(day=min(end.day, last_day))
            periods.append((shifted_start, shifted_end))
        return periods
    length = (end - start).days + 1
    if period_key == "current_week":
        return [(start - timedelta(days=7 * index), start - timedelta(days=7 * index) + timedelta(days=length - 1)) for index in range(1, count + 1)]
    periods = []
    period_end = start - timedelta(days=1)
    for _index in range(count):
        period_start = period_end - timedelta(days=length - 1)
        periods.append((period_start, period_end))
        period_end = period_start - timedelta(days=1)
    return periods


def pct_delta(current: Decimal | int, previous: Decimal | int) -> Decimal | None:
    current_dec = to_decimal_money(current)
    previous_dec = to_decimal_money(previous)
    if previous_dec == 0:
        return None
    return ((current_dec - previous_dec) / abs(previous_dec) * Decimal("100")).quantize(Decimal("0.01"))


def ratio_pct(numerator: Decimal | int, denominator: Decimal | int) -> Decimal | None:
    denom = to_decimal_money(denominator)
    if denom <= 0:
        return None
    return (to_decimal_money(numerator) / denom * Decimal("100")).quantize(Decimal("0.01"))


def merchant_features(
    *,
    current_total: Decimal | int,
    current_count: int,
    previous_total: Decimal | int = Decimal("0.00"),
    previous_count: int = 0,
    category_total: Decimal | int | None = None,
    scope_total: Decimal | int | None = None,
) -> dict[str, Any]:
    total = to_decimal_money(current_total)
    prev_total = to_decimal_money(previous_total)
    count = int(current_count or 0)
    prev_count = int(previous_count or 0)
    average = (total / Decimal(count)).quantize(Decimal("0.01")) if count else Decimal("0.00")
    previous_average = (prev_total / Decimal(prev_count)).quantize(Decimal("0.01")) if prev_count else Decimal("0.00")
    return {
        "total": total,
        "operation_count": count,
        "average_check": average,
        "previous_total": prev_total,
        "previous_operation_count": prev_count,
        "previous_average_check": previous_average,
        "amount_delta": total - prev_total,
        "amount_pct": pct_delta(total, prev_total),
        "frequency_delta": count - prev_count,
        "frequency_pct": pct_delta(Decimal(count), Decimal(prev_count)),
        "average_check_delta": average - previous_average,
        "average_check_pct": pct_delta(average, previous_average),
        "merchant_share_of_category": ratio_pct(total, category_total) if category_total is not None else None,
        "merchant_share_of_total": ratio_pct(total, scope_total) if scope_total is not None else None,
    }


def merchant_baseline(period_rows: Iterable[tuple[Decimal | int, int] | tuple[date, date, Decimal | int, int]], *, min_periods: int = BASELINE_MIN_PERIODS, min_operations: int = BASELINE_MIN_OPERATIONS) -> dict[str, Any]:
    normalized_rows = []
    periods = []
    for row in period_rows:
        if len(row) == 4:
            start, end, total, count = row
            normalized_rows.append((to_decimal_money(total), int(count or 0)))
            periods.append({"start_date": start, "end_date": end, "total": to_decimal_money(total), "count": int(count or 0)})
        else:
            total, count = row
            normalized_rows.append((to_decimal_money(total), int(count or 0)))
    completed = [(total, count) for total, count in normalized_rows if count > 0]
    observations = sum(count for _total, count in completed)
    if len(completed) < min_periods or observations < min_operations:
        return {
            "method": "trailing_median",
            "periods_used": len(completed),
            "amount": Decimal("0.00"),
            "count": 0,
            "average_check": Decimal("0.00"),
            "sufficient_data": False,
            "periods": periods,
        }
    amount = to_decimal_money(median([total for total, _count in completed]))
    count_median = Decimal(str(median([count for _total, count in completed]))).quantize(Decimal("0.01"))
    average = to_decimal_money(median([(total / Decimal(count)).quantize(Decimal("0.01")) for total, count in completed]))
    return {
        "method": "trailing_median",
        "periods_used": len(completed),
        "amount": amount,
        "count": count_median,
        "average_check": average,
        "sufficient_data": True,
        "periods": periods,
    }
