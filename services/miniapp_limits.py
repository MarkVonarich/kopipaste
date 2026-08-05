from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from psycopg2 import errors
from psycopg2.extras import Json

from db.database import get_conn
from db.queries import get_user_currency
from services.budgeting import DEFAULT_THRESHOLDS
from utils.money import to_decimal_money


@dataclass(frozen=True)
class StoredLimit:
    kind: str
    identifier: str
    title: str
    category: str | None
    amount: Decimal
    currency: str
    period: str
    workspace_id: int | None
    alerts_enabled: bool = True


class MiniAppLimitError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _limit_period(period: str) -> str:
    if period not in {"week", "month"}:
        raise MiniAppLimitError("bad_limit_period")
    return period


def _category_id(period: str, category: str) -> str:
    return f"category:{period}:{category}"


def _general_id(limit_id: int) -> str:
    return f"general:{int(limit_id)}"


def _general_row_to_limit(row) -> StoredLimit:
    return StoredLimit(
        kind="general",
        identifier=_general_id(row[0]),
        title=row[1],
        category=None,
        amount=to_decimal_money(row[2]),
        currency=row[3],
        period=row[4],
        workspace_id=int(row[5]) if row[5] is not None else None,
        alerts_enabled=bool(row[6]),
    )


def create_or_update_general_limit(
    *,
    user_id: int,
    workspace_id: int | None,
    name: str,
    amount: Decimal | int | str,
    period: str,
    currency: str | None = None,
    alerts_enabled: bool = True,
    limit_id: int | None = None,
) -> StoredLimit:
    period = _limit_period(period)
    amount_dec = to_decimal_money(amount, positive=True)
    cur_currency = (currency or get_user_currency(user_id) or "RUB")[:8]
    title = (name or "Все расходы").strip()[:80] or "Все расходы"
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if limit_id is None:
                cur.execute(
                    """
                    INSERT INTO public.general_spending_limits
                      (workspace_id, owner_user_id, name, amount, currency, period_type,
                       enabled, alerts_enabled, notification_thresholds)
                    VALUES (%s,%s,%s,%s,%s,%s,true,%s,%s)
                    RETURNING id, name, amount, currency, period_type, workspace_id, alerts_enabled
                    """,
                    (workspace_id, user_id, title, amount_dec, cur_currency, period, alerts_enabled, Json(list(DEFAULT_THRESHOLDS))),
                )
            else:
                cur.execute(
                    """
                    UPDATE public.general_spending_limits
                       SET name=%s,
                           amount=%s,
                           currency=%s,
                           period_type=%s,
                           enabled=true,
                           alerts_enabled=%s,
                           notification_thresholds=%s,
                           updated_at=now()
                     WHERE id=%s
                       AND owner_user_id=%s
                       AND workspace_id IS NOT DISTINCT FROM %s
                    RETURNING id, name, amount, currency, period_type, workspace_id, alerts_enabled
                    """,
                    (title, amount_dec, cur_currency, period, alerts_enabled, Json(list(DEFAULT_THRESHOLDS)), int(limit_id), user_id, workspace_id),
                )
            row = cur.fetchone()
            if not row:
                raise MiniAppLimitError("limit_not_found")
        conn.commit()
        return _general_row_to_limit(row)
    except errors.UniqueViolation as exc:
        conn.rollback()
        raise MiniAppLimitError("limit_conflict") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def replace_category_limit(
    *,
    user_id: int,
    workspace_id: int | None,
    old_period: str | None,
    old_category: str | None,
    period: str,
    category: str,
    amount: Decimal | int | str,
    currency: str,
    require_existing: bool = False,
) -> StoredLimit:
    period = _limit_period(period)
    if old_period is not None:
        old_period = _limit_period(old_period)
    category = str(category).strip()[:64]
    if not category:
        raise MiniAppLimitError("category_required")
    amount_dec = to_decimal_money(amount, positive=True)
    currency = (currency or get_user_currency(user_id) or "RUB")[:8]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if require_existing:
                cur.execute(
                    """
                    SELECT 1
                      FROM public.category_limits
                     WHERE user_id=%s
                       AND workspace_id IS NOT DISTINCT FROM %s
                       AND period=%s
                       AND category=%s
                     FOR UPDATE
                    """,
                    (user_id, workspace_id, old_period, old_category),
                )
                if not cur.fetchone():
                    raise MiniAppLimitError("limit_not_found")
            if old_period is not None and old_category is not None and (old_period != period or old_category != category):
                cur.execute(
                    """
                    DELETE FROM public.category_limits
                     WHERE user_id=%s
                       AND workspace_id IS NOT DISTINCT FROM %s
                       AND period=%s
                       AND category=%s
                    """,
                    (user_id, workspace_id, old_period, old_category),
                )
                if require_existing and cur.rowcount != 1:
                    raise MiniAppLimitError("limit_not_found")
            if old_period is None or old_category is None or old_period == period and old_category == category:
                cur.execute(
                    """
                    DELETE FROM public.category_limits
                     WHERE user_id=%s
                       AND workspace_id IS NOT DISTINCT FROM %s
                       AND period=%s
                       AND category=%s
                    """,
                    (user_id, workspace_id, period, category),
                )
            cur.execute(
                """
                INSERT INTO public.category_limits (user_id, workspace_id, period, category, amount, currency, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,now())
                RETURNING period, category, amount, currency, workspace_id
                """,
                (user_id, workspace_id, period, category, amount_dec, currency),
            )
            row = cur.fetchone()
        conn.commit()
        return StoredLimit(
            kind="category",
            identifier=_category_id(row[0], row[1]),
            title=row[1],
            category=row[1],
            amount=to_decimal_money(row[2]),
            currency=row[3],
            period=row[0],
            workspace_id=int(row[4]) if row[4] is not None else None,
            alerts_enabled=True,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_limit(*, user_id: int, workspace_id: int | None, limit_id: str) -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if limit_id.startswith("general:"):
                cur.execute(
                    """
                    DELETE FROM public.general_spending_limits
                     WHERE id=%s
                       AND owner_user_id=%s
                       AND workspace_id IS NOT DISTINCT FROM %s
                    """,
                    (int(limit_id.split(":", 1)[1]), user_id, workspace_id),
                )
            else:
                _kind, period, category = limit_id.split(":", 2)
                cur.execute(
                    """
                    DELETE FROM public.category_limits
                     WHERE user_id=%s
                       AND workspace_id IS NOT DISTINCT FROM %s
                       AND period=%s
                       AND category=%s
                    """,
                    (user_id, workspace_id, _limit_period(period), category),
                )
            deleted = cur.rowcount == 1
        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
