from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from typing import Iterable

from psycopg2 import errors
from psycopg2.extras import Json

from db.database import get_conn, pg_fetchall
from db.queries import get_user_currency
from services.i18n import format_money, normalize_locale
from utils.text import norm_text
from utils.money import to_decimal_money

EXPENSE_TYPE = "Расходы"
DEFAULT_THRESHOLDS = (70, 90, 100, 125, 150, 175, 200)


@dataclass(frozen=True)
class BudgetCategoryOption:
    token: str
    name: str
    normalized_name: str
    category_id: int | None = None
    source: str = "operation"


def _category_token(*, category_id: int | None, normalized_name: str) -> str:
    if category_id is not None:
        return f"c{int(category_id)}"
    digest = hashlib.blake2s(normalized_name.encode("utf-8"), digest_size=6).hexdigest()
    return f"o{digest}"


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class BudgetStatus:
    name: str
    amount: Decimal
    spent: Decimal
    currency: str
    period: Period
    categories: tuple[str, ...] = field(default_factory=tuple)

    @property
    def remaining(self) -> Decimal:
        return self.amount - self.spent

    @property
    def overage(self) -> Decimal:
        return max(Decimal("0.00"), self.spent - self.amount)

    @property
    def percentage(self) -> int:
        if self.amount <= 0:
            return 0
        return int(round(self.spent * 100 / self.amount))

    def days_remaining(self, today: date | None = None) -> int:
        today = today or date.today()
        return max(0, (self.period.end - today).days + 1)

    def average_daily_allowance_remaining(self, today: date | None = None) -> Decimal:
        days = max(1, self.days_remaining(today))
        return max(Decimal("0.00"), (self.remaining / Decimal(days)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def projected_end_spending(self, today: date | None = None) -> Decimal | None:
        today = today or date.today()
        elapsed = (min(today, self.period.end) - self.period.start).days + 1
        if elapsed < 3 or self.spent <= 0:
            return None
        return (self.spent / Decimal(elapsed) * Decimal(self.period.days)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def period_bounds(period_type: str, today: date | None = None, *, custom_start: date | None = None, custom_end: date | None = None, rolling_days: int | None = None) -> Period:
    today = today or date.today()
    if period_type == "week":
        start = today - timedelta(days=today.weekday())
        return Period(start, start + timedelta(days=6), "week")
    if period_type in {"month", "calendar_month"}:
        start = today.replace(day=1)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return Period(start, next_month - timedelta(days=1), "month")
    if period_type == "custom":
        if not custom_start or not custom_end or custom_end < custom_start:
            raise ValueError("invalid_custom_period")
        return Period(custom_start, custom_end, "custom")
    if period_type == "rolling_days":
        days = int(rolling_days or 0)
        if days <= 0:
            raise ValueError("invalid_rolling_days")
        return Period(today - timedelta(days=days - 1), today, f"rolling_{days}")
    raise ValueError(f"unknown period_type: {period_type}")


def expense_total(operations: Iterable[dict], period: Period, categories: Iterable[str] | None = None) -> Decimal:
    selected = set(categories or [])
    total = Decimal("0.00")
    for op in operations:
        op_date = op.get("op_date") or op.get("operation_date")
        if hasattr(op_date, "date"):
            op_date = op_date.date()
        if isinstance(op_date, str):
            op_date = date.fromisoformat(op_date)
        if op.get("type") != EXPENSE_TYPE:
            continue
        if op_date < period.start or op_date > period.end:
            continue
        if selected and op.get("category") not in selected:
            continue
        if op.get("category") == "Без операций":
            continue
        total += to_decimal_money(op.get("amount") or 0)
    return total


def top_category_contribution(operations: Iterable[dict], period: Period, categories: Iterable[str]) -> tuple[str, Decimal] | None:
    selected = set(categories)
    totals: dict[str, Decimal] = {}
    for op in operations:
        if op.get("type") != EXPENSE_TYPE or op.get("category") not in selected:
            continue
        op_date = op.get("op_date") or op.get("operation_date")
        if isinstance(op_date, str):
            op_date = date.fromisoformat(op_date)
        if hasattr(op_date, "date"):
            op_date = op_date.date()
        if period.start <= op_date <= period.end:
            cat = op.get("category") or ""
            totals[cat] = totals.get(cat, Decimal("0.00")) + to_decimal_money(op.get("amount") or 0)
    if not totals:
        return None
    return max(totals.items(), key=lambda item: item[1])


def build_budget_status(name: str, amount: Decimal | int | str, currency: str, period: Period, operations: Iterable[dict], categories: Iterable[str] | None = None) -> BudgetStatus:
    cats = tuple(dict.fromkeys(categories or ()))
    spent = expense_total(operations, period, cats or None)
    return BudgetStatus(name=name, amount=to_decimal_money(amount, positive=True), spent=spent, currency=currency, period=period, categories=cats)


def current_alert_milestone(percentage: int, thresholds: Iterable[int] = DEFAULT_THRESHOLDS) -> int | None:
    reached = [int(t) for t in thresholds if percentage >= int(t)]
    if not reached:
        return None
    return max(reached)


def render_limit_alert(status: BudgetStatus, *, locale: str | None = None, entity_label: str | None = None) -> str:
    lang = normalize_locale(locale)
    label = entity_label or status.name
    if status.overage > 0:
        if lang == "en":
            title = f"🚨 {label}: limit exceeded"
            over = "Overage"
            used = "Used"
        else:
            title = f"🚨 {label}: лимит превышен"
            over = "Превышение"
            used = "Использовано"
        return "\n".join([
            title,
            "",
            f"{'Limit' if lang == 'en' else 'Лимит'}: {format_money(status.amount, status.currency, lang)}",
            f"{'Spent' if lang == 'en' else 'Потрачено'}: {format_money(status.spent, status.currency, lang)}",
            f"{over}: {format_money(status.overage, status.currency, lang)}",
            f"{used}: {status.percentage}%",
        ])
    if lang == "en":
        title = f"⚠️ {label}: limit almost spent"
        remaining = "Remaining"
        used = "Used"
        until = "Days left"
        avg = "Average available"
    else:
        title = f"⚠️ {label}: лимит почти исчерпан"
        remaining = "Осталось"
        used = "Использовано"
        until = "До конца периода"
        avg = "Доступно в среднем"
    return "\n".join([
        title,
        "",
        f"{'Limit' if lang == 'en' else 'Лимит'}: {format_money(status.amount, status.currency, lang)}",
        f"{'Spent' if lang == 'en' else 'Потрачено'}: {format_money(status.spent, status.currency, lang)}",
        f"{remaining}: {format_money(status.remaining, status.currency, lang)}",
        f"{used}: {status.percentage}%",
        "",
        f"{until}: {status.days_remaining()}",
        f"{avg}: {format_money(status.average_daily_allowance_remaining(), status.currency, lang)} / {'day' if lang == 'en' else 'день'}",
    ])


def _period_args(period_type: str, period_start: date | None, period_end: date | None, rolling_days: int | None, today: date | None = None) -> Period:
    return period_bounds(period_type, today, custom_start=period_start, custom_end=period_end, rolling_days=rolling_days)


def list_general_limits(user_id: int, workspace_id: int | None = None) -> list[dict]:
    try:
        rows = pg_fetchall(
            """
            SELECT id, workspace_id, owner_user_id, name, amount, currency, period_type,
                   period_start, period_end, rolling_days, enabled, alerts_enabled, notification_thresholds
              FROM public.general_spending_limits
             WHERE owner_user_id=%s
               AND (%s::bigint IS NULL OR workspace_id=%s)
             ORDER BY enabled DESC, updated_at DESC, id DESC
            """,
            (user_id, workspace_id, workspace_id),
        )
    except errors.UndefinedTable:
        return []
    return [
        {
            "id": int(r[0]),
            "workspace_id": r[1],
            "owner_user_id": int(r[2]),
            "name": r[3],
            "amount": to_decimal_money(r[4]),
            "currency": r[5],
            "period_type": r[6],
            "period_start": r[7],
            "period_end": r[8],
            "rolling_days": r[9],
            "enabled": bool(r[10]),
            "alerts_enabled": bool(r[11]),
            "notification_thresholds": r[12] or list(DEFAULT_THRESHOLDS),
        }
        for r in rows
    ]


def upsert_general_limit(*, user_id: int, workspace_id: int | None, name: str, amount: Decimal | int | str, period_type: str = "month", currency: str | None = None, period_start: date | None = None, period_end: date | None = None, rolling_days: int | None = None, enabled: bool = True, alerts_enabled: bool = True, thresholds: Iterable[int] = DEFAULT_THRESHOLDS, limit_id: int | None = None) -> int:
    currency = currency or get_user_currency(user_id)
    amount_dec = to_decimal_money(amount, positive=True)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if limit_id:
                cur.execute(
                    """
                    UPDATE public.general_spending_limits
                       SET name=%s, amount=%s, currency=%s, period_type=%s,
                           period_start=%s, period_end=%s, rolling_days=%s,
                           enabled=%s, alerts_enabled=%s, notification_thresholds=%s,
                           updated_at=now()
                     WHERE id=%s AND owner_user_id=%s
                     RETURNING id
                    """,
                    (name, amount_dec, currency, period_type, period_start, period_end, rolling_days, enabled, alerts_enabled, Json(list(thresholds)), limit_id, user_id),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO public.general_spending_limits
                      (workspace_id, owner_user_id, name, amount, currency, period_type,
                       period_start, period_end, rolling_days, enabled, alerts_enabled, notification_thresholds)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (workspace_id, user_id, name, amount_dec, currency, period_type, period_start, period_end, rolling_days, enabled, alerts_enabled, Json(list(thresholds))),
                )
            row = cur.fetchone()
        conn.commit()
        return int(row[0])
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_category_budget_groups(user_id: int, workspace_id: int | None = None) -> list[dict]:
    try:
        rows = pg_fetchall(
            """
            SELECT g.id, g.workspace_id, g.owner_user_id, g.name, g.amount, g.currency,
                   g.period_type, g.enabled, g.alerts_enabled,
                   COALESCE(array_agg(m.category_name ORDER BY m.category_name) FILTER (WHERE m.category_name IS NOT NULL), '{}')
              FROM public.category_budget_groups g
              LEFT JOIN public.category_budget_group_members m ON m.group_id=g.id
             WHERE g.owner_user_id=%s
               AND (%s::bigint IS NULL OR g.workspace_id=%s)
             GROUP BY g.id
             ORDER BY g.enabled DESC, g.updated_at DESC, g.id DESC
            """,
            (user_id, workspace_id, workspace_id),
        )
    except errors.UndefinedTable:
        return []
    return [
        {
            "id": int(r[0]),
            "workspace_id": r[1],
            "owner_user_id": int(r[2]),
            "name": r[3],
            "amount": to_decimal_money(r[4]),
            "currency": r[5],
            "period_type": r[6],
            "enabled": bool(r[7]),
            "alerts_enabled": bool(r[8]),
            "categories": tuple(r[9] or ()),
        }
        for r in rows
    ]


def create_category_budget_group(*, user_id: int, workspace_id: int | None, name: str, amount: Decimal | int | str, categories: Iterable[str], currency: str | None = None, period_type: str = "month", enabled: bool = True, alerts_enabled: bool = True) -> int:
    unique_categories = list(dict.fromkeys(c.strip() for c in categories if c and c.strip()))
    if not unique_categories:
        raise ValueError("at least one category is required")
    currency = currency or get_user_currency(user_id)
    amount_dec = to_decimal_money(amount, positive=True)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.category_budget_groups
                  (workspace_id, owner_user_id, name, amount, currency, period_type, enabled, alerts_enabled)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (workspace_id, user_id, name.strip()[:120], amount_dec, currency, period_type, enabled, alerts_enabled),
            )
            group_id = int(cur.fetchone()[0])
            for category in unique_categories:
                cur.execute(
                    """
                    INSERT INTO public.category_budget_group_members (group_id, category_name, normalized_category_name)
                    VALUES (%s, %s, lower(%s))
                    ON CONFLICT DO NOTHING
                    """,
                    (group_id, category[:64], category[:64]),
                )
        conn.commit()
        return group_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_active_expense_categories(*, user_id: int, workspace_id: int | None = None, limit: int = 100) -> list[BudgetCategoryOption]:
    """Return current-workspace expense category choices without embedding names in callback_data."""
    found: dict[str, BudgetCategoryOption] = {}
    try:
        rows = pg_fetchall(
            """
            SELECT id, name
              FROM public.custom_categories
             WHERE workspace_id IS NOT DISTINCT FROM %s
               AND type=%s
               AND archived_at IS NULL
             ORDER BY name
             LIMIT %s
            """,
            (workspace_id, EXPENSE_TYPE, int(limit)),
        )
    except errors.UndefinedTable:
        rows = []
    for category_id, name in rows:
        if not name:
            continue
        normalized = norm_text(name).casefold()
        found[normalized] = BudgetCategoryOption(
            token=_category_token(category_id=int(category_id), normalized_name=normalized),
            name=name,
            normalized_name=normalized,
            category_id=int(category_id),
            source="custom",
        )

    remaining = max(0, int(limit) - len(found))
    if remaining:
        try:
            rows = pg_fetchall(
                """
                WITH ranked AS (
                    SELECT category, MAX(id) AS last_id, COUNT(*) AS op_count
                      FROM public.operations
                     WHERE type=%s
                       AND COALESCE(category,'') NOT IN ('', 'Без операций')
                       AND (%s::bigint IS NULL OR workspace_id=%s)
                       AND (%s::bigint IS NOT NULL OR user_id=%s OR chat_id=%s)
                     GROUP BY category
                )
                SELECT category
                  FROM ranked
                 ORDER BY op_count DESC, last_id DESC, category ASC
                 LIMIT %s
                """,
                (EXPENSE_TYPE, workspace_id, workspace_id, workspace_id, user_id, user_id, remaining),
            )
        except errors.UndefinedColumn:
            rows = pg_fetchall(
                """
                SELECT category
                  FROM public.operations
                 WHERE user_id=%s
                   AND type=%s
                   AND COALESCE(category,'') NOT IN ('', 'Без операций')
                 GROUP BY category
                 ORDER BY MAX(id) DESC, category ASC
                 LIMIT %s
                """,
                (user_id, EXPENSE_TYPE, remaining),
            )
        except errors.UndefinedTable:
            rows = []
        for (name,) in rows:
            normalized = norm_text(name).casefold()
            if not normalized or normalized in found:
                continue
            found[normalized] = BudgetCategoryOption(
                token=_category_token(category_id=None, normalized_name=normalized),
                name=name,
                normalized_name=normalized,
                category_id=None,
                source="operation",
            )

    return sorted(found.values(), key=lambda item: (item.name.casefold(), item.token))
