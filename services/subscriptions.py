from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from utils.money import to_decimal_money


SUBSCRIPTION_CATEGORIES = {"подписки", "subscriptions", "subscription"}


@dataclass(frozen=True)
class SubscriptionPrediction:
    merchant: str
    amount: Decimal
    currency: str
    previous_date: date
    expected_date: date
    confidence: float
    previous_operation_id: int | None = None

    @property
    def dedupe_key(self) -> str:
        return f"subscription:{normalize_merchant(self.merchant)}:{self.expected_date.isoformat()}:{self.currency}"


def normalize_merchant(value: str | None) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip().lower())
    text = re.sub(r"[^0-9a-zа-яё _.-]+", "", text)
    return text[:80]


def _next_month_same_day(dt: date) -> date:
    month = dt.month + 1
    year = dt.year
    if month > 12:
        month = 1
        year += 1
    first = date(year, month, 1)
    next_first = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
    last_day = (next_first - timedelta(days=1)).day
    return first.replace(day=min(dt.day, last_day))


def detect_upcoming_subscriptions(operations: list[dict], today: date, *, warn_days: int = 2, tolerance_days: int = 2) -> list[SubscriptionPrediction]:
    predictions: list[SubscriptionPrediction] = []
    by_merchant: dict[tuple[str, str], list[dict]] = {}
    for op in operations:
        if op.get("type") != "Расходы":
            continue
        category = (op.get("category") or "").strip().lower()
        merchant = normalize_merchant(op.get("merchant") or op.get("comment") or op.get("raw_text"))
        if category not in SUBSCRIPTION_CATEGORIES and "подпис" not in category and "subscription" not in merchant:
            continue
        if not merchant:
            continue
        by_merchant.setdefault((merchant, op.get("currency") or "RUB"), []).append(op)
    for (merchant, currency), rows in by_merchant.items():
        rows = sorted(rows, key=lambda r: r["op_date"])
        last = rows[-1]
        expected = _next_month_same_day(last["op_date"])
        if abs((expected - today).days - warn_days) > tolerance_days:
            continue
        confidence = 0.85 if len(rows) >= 2 else 0.6
        predictions.append(SubscriptionPrediction(
            merchant=merchant,
            amount=to_decimal_money(last.get("amount") or 0),
            currency=currency,
            previous_date=last["op_date"],
            expected_date=expected,
            confidence=confidence,
            previous_operation_id=last.get("id"),
        ))
    return predictions
