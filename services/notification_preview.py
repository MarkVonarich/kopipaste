from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from psycopg2 import errors

from db.database import pg_fetchall
from db.queries import get_user_currency, get_user_locale
from services.budgeting import build_budget_status, period_bounds, render_limit_alert
from services.notification_engine import NotificationFact, fallback_fact
from services.recurring_spend import detect_recurring_spend
from services.subscriptions import detect_upcoming_subscriptions


@dataclass(frozen=True)
class NotificationPreview:
    user_id: int
    kind: str
    fact: NotificationFact
    reason: str
    final_text: str


def _recent_operations(user_id: int, limit: int = 120) -> list[dict]:
    currency = _safe_currency(user_id)
    try:
        rows = pg_fetchall(
            """
            SELECT id, op_date, type, category, amount, COALESCE(comment,''), COALESCE(currency, %s), workspace_id
              FROM public.operations
             WHERE user_id=%s
               AND COALESCE(type,'') <> 'noop'
             ORDER BY op_date DESC, id DESC
             LIMIT %s
            """,
            (currency, user_id, int(limit)),
        )
    except Exception:
        rows = []
    return [
        {
            "id": int(r[0]),
            "op_date": r[1],
            "type": r[2],
            "category": r[3],
            "amount": int(r[4] or 0),
            "comment": r[5],
            "currency": r[6],
            "workspace_id": r[7],
        }
        for r in rows
    ]


def _safe_currency(user_id: int) -> str:
    try:
        return get_user_currency(user_id)
    except Exception:
        return "RUB"


def _safe_locale(user_id: int) -> str:
    try:
        return get_user_locale(user_id)
    except Exception:
        return "ru"


def build_preview(user_id: int, kind: str = "auto", today: date | None = None) -> NotificationPreview:
    today = today or date.today()
    locale = _safe_locale(user_id)
    operations = _recent_operations(user_id)
    facts: list[tuple[NotificationFact, str]] = []
    if kind in {"auto", "subscription"}:
        for item in detect_upcoming_subscriptions(operations, today):
            text = f"Скоро возможна подписка: {item.merchant} — {item.amount} {item.currency}, ожидается {item.expected_date.isoformat()}"
            facts.append((NotificationFact("subscription_upcoming", item.dedupe_key, text, 10, item.workspace_id, "subscription", item.previous_operation_id, {"expected_date": item.expected_date.isoformat(), "amount": item.amount}), "upcoming subscription has highest priority"))
    if kind in {"auto", "recurring-spend"}:
        for item in detect_recurring_spend(operations):
            text = f"Повторяющаяся трата: {item.merchant} — примерно {item.monthly_estimate} {item.currency} в месяц"
            facts.append((NotificationFact("recurring_spend_detected", item.dedupe_key, text, 50, None, "recurring_spend", None, {"count": item.count, "monthly_estimate": item.monthly_estimate}), "recurring spend pattern detected"))
    if kind in {"auto", "limit"}:
        period = period_bounds("month", today)
        expenses = [op for op in operations if op.get("type") == "Расходы"]
        if expenses:
            amount = max(1, sum(int(op.get("amount") or 0) for op in expenses[:10]))
            status = build_budget_status("Расходы месяца", amount, get_user_currency(user_id), period, operations)
            text = render_limit_alert(status, locale=locale)
            facts.append((NotificationFact("limit_near", f"preview:limit:{user_id}:{today.isoformat()}", text, 30, None, "general_limit", None, {"spent": status.spent, "limit": status.amount, "percentage": status.percentage}), "limit preview built from recent user spending"))
    if not facts:
        fact = fallback_fact(user_id, today.isoformat(), locale)
        return NotificationPreview(user_id, kind, fact, "no stronger personalized fact available", fact.text)
    fact, reason = sorted(facts, key=lambda pair: (pair[0].priority, pair[0].notification_type, pair[0].dedupe_key))[0]
    return NotificationPreview(user_id, kind, fact, reason, fact.text)


def render_admin_preview(preview: NotificationPreview) -> str:
    fact = preview.fact
    payload_lines = [f"{k}: {v}" for k, v in sorted((fact.payload or {}).items()) if k not in {"raw_text", "comment"}]
    return "\n".join([
        "ADMIN PREVIEW — NOT SENT TO USER",
        "",
        f"Target user: {preview.user_id}",
        f"Kind: {preview.kind}",
        f"Selected fact: {fact.notification_type}",
        f"Priority: {fact.priority}",
        f"Dedupe key: {fact.dedupe_key}",
        f"Why this fact won: {preview.reason}",
        "",
        "Calculated values:",
        *(payload_lines or ["none"]),
        "",
        "Final user-facing message:",
        preview.final_text,
    ])
