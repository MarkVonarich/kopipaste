from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import Any, Iterable

from utils.money import to_decimal_money

EMPTY_MERCHANT_KEY = "__empty_merchant__"
SYNTHETIC_OTHER_MERCHANT_KEY = "__synthetic_other_merchant__"
BASELINE_MIN_PERIODS = 3
BASELINE_MIN_OPERATIONS = 3

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class MerchantIdentity:
    key: str
    display_name: str
    source: str
    raw_aliases: tuple[str, ...]
    drillable: bool = True
    fallback: bool = False


def _clean_unicode(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "")


def normalize_merchant_key(value: str | None) -> str:
    """Return a conservative deterministic merchant key without semantic guessing."""
    text = _clean_unicode(value).strip().casefold().replace("ё", "е")
    chars = [char if char.isalnum() else " " for char in text]
    return _WHITESPACE_RE.sub(" ", "".join(chars)).strip()[:120]


def clean_merchant_display(value: str | None) -> str:
    text = _clean_unicode(value).strip()
    chars = [char if (char.isalnum() or char.isspace()) else " " for char in text]
    return _WHITESPACE_RE.sub(" ", "".join(chars)).strip()[:120]


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
    original = _clean_unicode(raw).strip()
    punctuation_penalty = 1 if clean_merchant_display(original) != original else 0
    uppercase_penalty = 1 if _has_cased_letters(original) and original == original.upper() else 0
    return (
        punctuation_penalty + uppercase_penalty,
        -int(stats.get("count") or 0),
        -to_decimal_money(stats.get("total") or 0),
        len(display),
        display.casefold(),
    )


def choose_display_name(raw_values: dict[str, dict[str, Any]], key: str) -> str:
    if not raw_values:
        return key
    raw, _stats = sorted(raw_values.items(), key=_display_sort_key)[0]
    display = _display_candidate(raw)
    return display or key


def merchant_identity(raw_value: str | None) -> MerchantIdentity:
    raw = _clean_unicode(raw_value).strip()
    key = normalize_merchant_key(raw)
    if not key:
        return MerchantIdentity(EMPTY_MERCHANT_KEY, "Без описания", "fallback_empty", (), drillable=False, fallback=True)
    display = _display_candidate(raw) or key
    return MerchantIdentity(key, display, "deterministic", (raw,), drillable=True, fallback=False)


def merchant_key_sql(column: str) -> str:
    return (
        "TRIM(REGEXP_REPLACE("
        f"REGEXP_REPLACE(REPLACE(LOWER(COALESCE({column},'')), 'ё', 'е'), '[[:punct:]]+', ' ', 'g'), "
        "'\\s+', ' ', 'g'))"
    )


def fold_merchant_rows(rows: Iterable[tuple[str | None, str, Decimal, int]]) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for raw_name, currency, total, count in rows:
        currency_code = str(currency)
        raw = _clean_unicode(raw_name).strip()
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
        key=lambda item: (-int(item[1].get("count") or 0), -to_decimal_money(item[1].get("total") or 0), clean_merchant_display(item[0]).casefold()),
    )
    return [clean_merchant_display(raw) or raw for raw, _stats in aliases[:limit]]


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


def merchant_baseline(period_rows: Iterable[tuple[Decimal | int, int]], *, min_periods: int = BASELINE_MIN_PERIODS, min_operations: int = BASELINE_MIN_OPERATIONS) -> dict[str, Any]:
    completed = [(to_decimal_money(total), int(count or 0)) for total, count in period_rows if int(count or 0) > 0]
    observations = sum(count for _total, count in completed)
    if len(completed) < min_periods or observations < min_operations:
        return {
            "method": "trailing_median",
            "periods_used": len(completed),
            "amount": Decimal("0.00"),
            "count": 0,
            "average_check": Decimal("0.00"),
            "sufficient_data": False,
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
    }
