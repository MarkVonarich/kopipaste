from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from services.subscriptions import normalize_merchant
from utils.money import to_decimal_money


@dataclass(frozen=True)
class RecurringSpendInsight:
    merchant: str
    category: str
    currency: str
    count: int
    total: Decimal
    average_amount: Decimal
    monthly_estimate: Decimal
    cadence: str
    confidence: float

    @property
    def dedupe_key(self) -> str:
        return f"recurring:{self.merchant}:{self.category}:{self.currency}"


def detect_recurring_spend(operations: list[dict], *, window_days: int = 60, min_count: int = 3, amount_tolerance: float = 0.35) -> list[RecurringSpendInsight]:
    if not operations:
        return []
    end = max(op["op_date"] for op in operations)
    start = end.fromordinal(end.toordinal() - window_days + 1)
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for op in operations:
        if op.get("type") != "Расходы" or not (start <= op["op_date"] <= end):
            continue
        merchant = normalize_merchant(op.get("merchant") or op.get("comment") or op.get("raw_text"))
        if not merchant:
            continue
        key = (merchant, op.get("category") or "Прочее", op.get("currency") or "RUB")
        groups.setdefault(key, []).append(op)
    insights: list[RecurringSpendInsight] = []
    for (merchant, category, currency), rows in groups.items():
        if len(rows) < min_count:
            continue
        amounts = [to_decimal_money(r.get("amount") or 0) for r in rows]
        avg = (sum(amounts, Decimal("0.00")) / Decimal(len(amounts))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if avg <= 0:
            continue
        max_deviation = max(abs(a - avg) / avg for a in amounts)
        if max_deviation > amount_tolerance:
            continue
        total = sum(amounts)
        days = max(1, (end - start).days + 1)
        monthly = (total / Decimal(days) * Decimal(30)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        cadence = "weekly" if len(rows) >= max(3, window_days // 14) else "repeated"
        confidence = min(0.95, 0.45 + len(rows) * 0.08 + (0.2 if max_deviation <= 0.15 else 0))
        insights.append(RecurringSpendInsight(merchant, category, currency, len(rows), total, avg, monthly, cadence, confidence))
    return sorted(insights, key=lambda i: (-i.confidence, -i.monthly_estimate, i.merchant))
