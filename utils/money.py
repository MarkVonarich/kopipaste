from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

MONEY_QUANT = Decimal("0.01")


class MoneyParseError(ValueError):
    pass


def to_decimal_money(value: Any, *, positive: bool = False, allow_zero: bool = True) -> Decimal:
    try:
        amount = Decimal(str(value).strip()).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise MoneyParseError("bad_amount") from exc
    if positive and amount <= 0:
        raise MoneyParseError("bad_amount")
    if not allow_zero and amount == 0:
        raise MoneyParseError("bad_amount")
    return amount


def parse_decimal_amount_token(raw: str) -> Decimal:
    text = (raw or "").strip().replace("\u00a0", " ")
    if not text or not all(ch.isdigit() or ch in " .," for ch in text):
        raise MoneyParseError("bad_amount")

    has_space_grouping = " " in text
    compact = text.replace(" ", "")
    if "." in compact and "," in compact:
        raise MoneyParseError("bad_amount")

    sep = "," if "," in compact else "." if "." in compact else None
    integer_part = compact
    fraction = ""
    if sep:
        parts = compact.split(sep)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise MoneyParseError("bad_amount")
        integer_part, fraction = parts
        if len(fraction) > 2:
            raise MoneyParseError("bad_amount")

    if has_space_grouping:
        groups = text.split(sep, 1)[0] if sep else text
        pieces = [p for p in groups.split(" ") if p]
        if len(pieces) < 2 or not pieces[0].isdigit() or any(not p.isdigit() or len(p) != 3 for p in pieces[1:]):
            raise MoneyParseError("bad_amount")

    if not integer_part.isdigit():
        raise MoneyParseError("bad_amount")
    normalized = integer_part + (f".{fraction.ljust(2, '0')}" if sep else ".00")
    amount = to_decimal_money(normalized, positive=True)
    return amount


def format_money(value: Any, currency: str = "RUB", *, locale: str = "ru", symbol: bool = True) -> str:
    amount = to_decimal_money(value)
    raw = f"{int(amount):,}" if amount == amount.to_integral_value() else f"{amount:,.2f}"
    if (locale or "ru").lower().startswith("ru"):
        raw = raw.replace(",", " ").replace(".", ",")
    code = (currency or "RUB").upper()
    suffix = {"RUB": "₽", "USD": "$", "EUR": "€"}.get(code, code) if symbol else code
    return f"{raw} {suffix}".strip()
