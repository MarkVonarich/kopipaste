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
    enabled: bool = True


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
        enabled=bool(row[6]),
        alerts_enabled=bool(row[7]),
    )


def create_or_update_general_limit_tx(
    cur,
    *,
    user_id: int,
    workspace_id: int | None,
    name: str,
    amount: Decimal | int | str,
    period: str,
    currency: str,
    alerts_enabled: bool = True,
    limit_id: int | None = None,
) -> StoredLimit:
    period = _limit_period(period)
    amount_dec = to_decimal_money(amount, positive=True)
    cur_currency = (currency[:8] if currency else None)
    title = (name or "Все расходы").strip()[:80] or "Все расходы"
    if limit_id is None:
        cur_currency = cur_currency or (get_user_currency(user_id) or "RUB")[:8]
        cur.execute(
            """
            INSERT INTO public.general_spending_limits
              (workspace_id, owner_user_id, name, amount, currency, period_type,
               enabled, alerts_enabled, notification_thresholds)
            VALUES (%s,%s,%s,%s,%s,%s,true,%s,%s)
            RETURNING id, name, amount, currency, period_type, workspace_id, enabled, alerts_enabled
            """,
            (workspace_id, user_id, title, amount_dec, cur_currency, period, alerts_enabled, Json(list(DEFAULT_THRESHOLDS))),
        )
    else:
        cur.execute(
            """
            UPDATE public.general_spending_limits
               SET name=%s,
                   amount=%s,
                   currency=COALESCE(%s, currency),
                   period_type=%s,
                   alerts_enabled=%s,
                   notification_thresholds=%s,
                   updated_at=now()
             WHERE id=%s
               AND owner_user_id=%s
               AND workspace_id IS NOT DISTINCT FROM %s
            RETURNING id, name, amount, currency, period_type, workspace_id, enabled, alerts_enabled
            """,
            (title, amount_dec, cur_currency, period, alerts_enabled, Json(list(DEFAULT_THRESHOLDS)), int(limit_id), user_id, workspace_id),
        )
    row = cur.fetchone()
    if not row:
        raise MiniAppLimitError("limit_not_found")
    return _general_row_to_limit(row)


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
    cur_currency = (currency[:8] if currency else None)
    title = (name or "Все расходы").strip()[:80] or "Все расходы"
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            stored = create_or_update_general_limit_tx(
                cur,
                user_id=user_id,
                workspace_id=workspace_id,
                name=title,
                amount=amount_dec,
                period=period,
                currency=cur_currency,
                alerts_enabled=alerts_enabled,
                limit_id=limit_id,
            )
        conn.commit()
        return stored
    except errors.UniqueViolation as exc:
        conn.rollback()
        raise MiniAppLimitError("limit_conflict") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_general_limit_enabled(
    *,
    user_id: int,
    workspace_id: int | None,
    limit_id: int,
    enabled: bool | None = None,
    alerts_enabled: bool | None = None,
) -> StoredLimit:
    assignments: list[str] = []
    values: list = []
    if enabled is not None:
        assignments.append("enabled=%s")
        values.append(bool(enabled))
    if alerts_enabled is not None:
        assignments.append("alerts_enabled=%s")
        values.append(bool(alerts_enabled))
    assignments.append("updated_at=now()")
    values.extend([int(limit_id), int(user_id), workspace_id])
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE public.general_spending_limits
                   SET {', '.join(assignments)}
                 WHERE id=%s
                   AND owner_user_id=%s
                   AND workspace_id IS NOT DISTINCT FROM %s
                RETURNING id, name, amount, currency, period_type, workspace_id, enabled, alerts_enabled
                """,
                tuple(values),
            )
            row = cur.fetchone()
            if not row:
                raise MiniAppLimitError("limit_not_found")
        conn.commit()
        return _general_row_to_limit(row)
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
    title: str | None = None,
    alerts_enabled: bool | None = True,
    require_existing: bool = False,
) -> StoredLimit:
    period = _limit_period(period)
    if old_period is not None:
        old_period = _limit_period(old_period)
    category = str(category).strip()[:64]
    if not category:
        raise MiniAppLimitError("category_required")
    amount_dec = to_decimal_money(amount, positive=True)
    currency = (currency[:8] if currency else None)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            stored = replace_category_limit_tx(
                cur,
                user_id=user_id,
                workspace_id=workspace_id,
                old_period=old_period,
                old_category=old_category,
                period=period,
                category=category,
                amount=amount_dec,
                currency=currency,
                title=title,
                alerts_enabled=alerts_enabled,
                require_existing=require_existing,
            )
        conn.commit()
        return stored
    except errors.UniqueViolation as exc:
        conn.rollback()
        raise MiniAppLimitError("limit_conflict") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def replace_category_limit_tx(
    cur,
    *,
    user_id: int,
    workspace_id: int | None,
    old_period: str | None,
    old_category: str | None,
    period: str,
    category: str,
    amount: Decimal | int | str,
    currency: str,
    title: str | None = None,
    alerts_enabled: bool | None = True,
    require_existing: bool = False,
) -> StoredLimit:
    period = _limit_period(period)
    if old_period is not None:
        old_period = _limit_period(old_period)
    category = str(category).strip()[:64]
    if not category:
        raise MiniAppLimitError("category_required")
    amount_dec = to_decimal_money(amount, positive=True)
    currency = (currency[:8] if currency else None)
    display_name = str(title or category).strip()[:80] or category
    if require_existing:
        cur.execute(
            """
            SELECT currency, COALESCE(display_name, category), alerts_enabled
              FROM public.category_limits
             WHERE user_id=%s
               AND workspace_id IS NOT DISTINCT FROM %s
               AND period=%s
               AND category=%s
             FOR UPDATE
            """,
            (user_id, workspace_id, old_period, old_category),
        )
        existing = cur.fetchone()
        if not existing:
            raise MiniAppLimitError("limit_not_found")
        currency = currency or existing[0]
        if title is None:
            display_name = str(existing[1] or category).strip()[:80] or category
        if alerts_enabled is None:
            alerts_enabled = bool(existing[2])
    if alerts_enabled is None:
        alerts_enabled = True
    currency = currency or (get_user_currency(user_id) or "RUB")[:8]
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"category-limit:{user_id}:{workspace_id}:{period}:{category.casefold()}",))
    if require_existing:
        cur.execute(
            """
            SELECT 1
              FROM public.category_limits
             WHERE user_id=%s
               AND workspace_id IS NOT DISTINCT FROM %s
               AND period=%s
               AND category=%s
               AND NOT (period=%s AND category=%s)
             LIMIT 1
            """,
            (user_id, workspace_id, period, category, old_period, old_category),
        )
        if cur.fetchone():
            raise MiniAppLimitError("limit_conflict")
        cur.execute(
            """
            UPDATE public.category_limits
               SET period=%s,
                   category=%s,
                   amount=%s,
                   currency=%s,
                   display_name=%s,
                   alerts_enabled=%s,
                   updated_at=now()
             WHERE user_id=%s
               AND workspace_id IS NOT DISTINCT FROM %s
               AND period=%s
               AND category=%s
            RETURNING period, category, amount, currency, workspace_id, COALESCE(display_name, category), alerts_enabled
            """,
            (period, category, amount_dec, currency, display_name, bool(alerts_enabled), user_id, workspace_id, old_period, old_category),
        )
        row = cur.fetchone()
        if not row:
            raise MiniAppLimitError("limit_not_found")
    else:
        cur.execute(
            """
            SELECT 1 FROM public.category_limits
             WHERE user_id=%s AND workspace_id IS NOT DISTINCT FROM %s
               AND period=%s AND category=%s LIMIT 1
            """,
            (user_id, workspace_id, period, category),
        )
        if cur.fetchone():
            raise MiniAppLimitError("limit_conflict")
        cur.execute(
            """
            INSERT INTO public.category_limits
              (user_id, workspace_id, period, category, amount, currency, display_name, alerts_enabled, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now())
            RETURNING period, category, amount, currency, workspace_id, COALESCE(display_name, category), alerts_enabled
            """,
            (user_id, workspace_id, period, category, amount_dec, currency, display_name, bool(alerts_enabled)),
        )
        row = cur.fetchone()
    return StoredLimit(
        kind="category",
        identifier=_category_id(row[0], row[1]),
        title=row[5],
        category=row[1],
        amount=to_decimal_money(row[2]),
        currency=row[3],
        period=row[0],
        workspace_id=int(row[4]) if row[4] is not None else None,
        alerts_enabled=bool(row[6]),
        enabled=True,
    )


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
