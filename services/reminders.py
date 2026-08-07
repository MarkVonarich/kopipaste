from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable

from psycopg2 import errors

from db.database import get_conn, pg_fetchall
from db.queries import get_user_currency
from services.operations import RecordedOperation, insert_financial_operation_tx, record_financial_operation_post_commit
from services.workspaces import WorkspaceContext
from utils.money import to_decimal_money

REMINDER_TYPES = {"Расходы", "Доходы"}
REPEAT_RULES = {"none", "weekly", "monthly", "yearly", "custom_days"}


class ReminderError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ReminderRecordResult:
    status: str
    reminder: dict[str, Any] | None
    operation: RecordedOperation | None = None


def _next_monthly_date(value: date) -> date:
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    day = min(value.day, 28)
    return date(year, month, day)


def _advance_date(value: date, repeat_rule: str, interval_days: int | None = None) -> date | None:
    if repeat_rule == "none":
        return None
    if repeat_rule == "weekly":
        return value + timedelta(days=7)
    if repeat_rule == "monthly":
        return _next_monthly_date(value)
    if repeat_rule == "yearly":
        try:
            return value.replace(year=value.year + 1)
        except ValueError:
            return value.replace(month=2, day=28, year=value.year + 1)
    if repeat_rule == "custom_days":
        return value + timedelta(days=int(interval_days or 1))
    raise ReminderError("reminder_invalid_repeat")


def _date_value(value: Any, *, code: str = "reminder_invalid_date") -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ReminderError(code) from exc


def _row_to_reminder(row, *, today: date | None = None) -> dict[str, Any]:
    event_date = _date_value(row[6])
    is_active = bool(row[10])
    if not is_active:
        status = "inactive"
    elif today and event_date < today:
        status = "overdue"
    elif today and event_date == today:
        status = "today"
    else:
        status = "upcoming"
    amount = to_decimal_money(row[4])
    repeat_rule = str(row[7] or "none")
    return {
        "id": int(row[0]),
        "title": str(row[1] or ""),
        "rem_type": str(row[2] or "Расходы"),
        "category": str(row[3] or "Прочее"),
        "amount": amount,
        "currency": str(row[5] or "RUB"),
        "event_date": event_date,
        "repeat_rule": repeat_rule,
        "repeat_interval_days": int(row[8]) if row[8] is not None else None,
        "notify_days_before": int(row[9] or 0),
        "is_active": is_active,
        "status": status,
        "next_event_date": _advance_date(event_date, repeat_rule, int(row[8]) if row[8] is not None else None) if repeat_rule != "none" else None,
    }


def _normalize_payload(payload: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not partial or "title" in payload:
        title = str(payload.get("title") or "").strip()[:120]
        if not title:
            raise ReminderError("reminder_title_required")
        out["title"] = title
    if not partial or "amount" in payload:
        out["amount"] = to_decimal_money(payload.get("amount"), positive=True)
    if not partial or "currency" in payload:
        out["currency"] = str(payload.get("currency") or "RUB").strip().upper()[:8] or "RUB"
    if not partial or "category" in payload:
        category = str(payload.get("category") or "").strip()[:64]
        if not category:
            raise ReminderError("reminder_category_required")
        out["category"] = category
    if not partial or "rem_type" in payload:
        rem_type = str(payload.get("rem_type") or "Расходы")
        if rem_type not in REMINDER_TYPES:
            raise ReminderError("reminder_invalid_type")
        out["rem_type"] = rem_type
    if not partial or "event_date" in payload:
        out["event_date"] = _date_value(payload.get("event_date"))
    if not partial or "repeat_rule" in payload or "repeat_interval_days" in payload:
        repeat_rule = str(payload.get("repeat_rule") or ("none" if not partial else ""))
        if partial and not repeat_rule and "repeat_interval_days" in payload:
            repeat_rule = "custom_days"
        if repeat_rule:
            if repeat_rule not in REPEAT_RULES:
                raise ReminderError("reminder_invalid_repeat")
            out["repeat_rule"] = repeat_rule
            if repeat_rule == "custom_days":
                try:
                    interval = int(payload.get("repeat_interval_days") or 0)
                except (TypeError, ValueError) as exc:
                    raise ReminderError("reminder_invalid_repeat") from exc
                if interval < 1 or interval > 3650:
                    raise ReminderError("reminder_invalid_repeat")
                out["repeat_interval_days"] = interval
            else:
                out["repeat_interval_days"] = None
    if not partial or "notify_days_before" in payload:
        try:
            notify_days = int(payload.get("notify_days_before") if payload.get("notify_days_before") is not None else 1)
        except (TypeError, ValueError) as exc:
            raise ReminderError("reminder_invalid_notify") from exc
        if notify_days < 0 or notify_days > 30:
            raise ReminderError("reminder_invalid_notify")
        out["notify_days_before"] = notify_days
    if "is_active" in payload:
        out["is_active"] = bool(payload.get("is_active"))
    return out


def list_reminders(user_id: int, *, active_only: bool = False, today: date | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT id, title, rem_type, category, amount, currency, event_date,
               repeat_rule, repeat_interval_days, notify_days_before, is_active
          FROM public.user_reminders
         WHERE user_id=%s
    """
    params: list[Any] = [int(user_id)]
    if active_only:
        sql += " AND is_active=TRUE"
    sql += " ORDER BY event_date, id"
    try:
        rows = pg_fetchall(sql, tuple(params))
    except errors.UndefinedTable:
        return []
    reminders = [_row_to_reminder(row, today=today) for row in rows]

    def sort_key(item: dict[str, Any]) -> tuple[int, date, int]:
        status_rank = {"overdue": 0, "today": 1, "upcoming": 2, "inactive": 3}
        return (status_rank.get(str(item["status"]), 4), item["event_date"], int(item["id"]))

    return sorted(reminders, key=sort_key)


def get_reminder(user_id: int, reminder_id: int, *, today: date | None = None) -> dict[str, Any] | None:
    try:
        rows = pg_fetchall(
            """
            SELECT id, title, rem_type, category, amount, currency, event_date,
                   repeat_rule, repeat_interval_days, notify_days_before, is_active
              FROM public.user_reminders
             WHERE user_id=%s AND id=%s
             LIMIT 1
            """,
            (int(user_id), int(reminder_id)),
        )
    except errors.UndefinedTable:
        return None
    return _row_to_reminder(rows[0], today=today) if rows else None


def create_reminder(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    values = _normalize_payload({**payload, "currency": payload.get("currency") or get_user_currency(user_id)})
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.user_reminders
                  (user_id, title, rem_type, category, amount, currency, event_date,
                   repeat_rule, repeat_interval_days, notify_days_before, is_active, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                RETURNING id
                """,
                (
                    int(user_id),
                    values["title"],
                    values["rem_type"],
                    values["category"],
                    values["amount"],
                    values["currency"],
                    values["event_date"],
                    values["repeat_rule"],
                    values.get("repeat_interval_days"),
                    values["notify_days_before"],
                    bool(payload.get("is_active", True)),
                ),
            )
            reminder_id = int(cur.fetchone()[0])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    item = get_reminder(user_id, reminder_id)
    if not item:
        raise ReminderError("reminder_not_found")
    return item


def update_reminder(user_id: int, reminder_id: int, **fields: Any) -> dict[str, Any]:
    values = _normalize_payload(fields, partial=True)
    if not values:
        item = get_reminder(user_id, reminder_id)
        if not item:
            raise ReminderError("reminder_not_found")
        return item
    assignments = [f"{key}=%s" for key in values]
    params = [*values.values(), int(user_id), int(reminder_id)]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE public.user_reminders
                   SET {', '.join(assignments)}, updated_at=now()
                 WHERE user_id=%s AND id=%s
                 RETURNING id
                """,
                tuple(params),
            )
            if not cur.fetchone():
                raise ReminderError("reminder_not_found")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    item = get_reminder(user_id, reminder_id)
    if not item:
        raise ReminderError("reminder_not_found")
    return item


def toggle_reminder(user_id: int, reminder_id: int, enabled: bool | None = None) -> dict[str, Any]:
    current = get_reminder(user_id, reminder_id)
    if not current:
        raise ReminderError("reminder_not_found")
    return update_reminder(user_id, reminder_id, is_active=(not current["is_active"] if enabled is None else bool(enabled)))


def snooze_reminder(user_id: int, reminder_id: int, *, days: int = 1) -> dict[str, Any]:
    current = get_reminder(user_id, reminder_id)
    if not current:
        raise ReminderError("reminder_not_found")
    return update_reminder(user_id, reminder_id, event_date=current["event_date"] + timedelta(days=max(1, int(days))))


def delete_reminder(user_id: int, reminder_id: int) -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM public.user_reminders WHERE user_id=%s AND id=%s", (int(user_id), int(reminder_id)))
            deleted = cur.rowcount == 1
        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_reminder_tx(
    cur,
    *,
    user_id: int,
    reminder_id: int,
    workspace: WorkspaceContext,
    chat_type: str,
    expected_event_date: date | None = None,
) -> ReminderRecordResult:
    cur.execute(
        """
        SELECT id, title, rem_type, category, amount, currency, event_date,
               repeat_rule, repeat_interval_days, notify_days_before, is_active
          FROM public.user_reminders
         WHERE user_id=%s AND id=%s
         FOR UPDATE
        """,
        (int(user_id), int(reminder_id)),
    )
    row = cur.fetchone()
    if not row:
        raise ReminderError("reminder_not_found")
    reminder = _row_to_reminder(row)
    if not reminder["is_active"]:
        raise ReminderError("reminder_inactive")
    occurrence = reminder["event_date"]
    if expected_event_date and expected_event_date != occurrence:
        cur.execute(
            """
            SELECT 1
              FROM public.user_reminder_events
             WHERE reminder_id=%s AND user_id=%s AND event_date=%s AND event_type='recorded'
             LIMIT 1
            """,
            (int(reminder_id), int(user_id), expected_event_date),
        )
        if cur.fetchone():
            return ReminderRecordResult("already_recorded", reminder)
        raise ReminderError("reminder_stale_occurrence")
    cur.execute(
        """
        SELECT 1
          FROM public.user_reminder_events
         WHERE reminder_id=%s AND user_id=%s AND event_date=%s AND event_type='recorded'
         LIMIT 1
        """,
        (int(reminder_id), int(user_id), occurrence),
    )
    if cur.fetchone():
        return ReminderRecordResult("already_recorded", reminder)
    recorded = insert_financial_operation_tx(
        cur,
        chat_id=workspace.chat_id,
        actor_user_id=int(user_id),
        op_date=occurrence,
        op_type=reminder["rem_type"],
        category=reminder["category"],
        amount=reminder["amount"],
        comment=reminder["title"],
        source="reminder",
        chat_type=chat_type,
        workspace=workspace,
        raw_text=None,
    )
    cur.execute(
        """
        INSERT INTO public.user_reminder_events(reminder_id, user_id, event_date, notify_days_before, event_type)
        VALUES (%s,%s,%s,%s,'recorded')
        """,
        (int(reminder_id), int(user_id), occurrence, int(reminder["notify_days_before"])),
    )
    next_date = _advance_date(occurrence, reminder["repeat_rule"], reminder.get("repeat_interval_days"))
    if next_date is None:
        cur.execute("UPDATE public.user_reminders SET is_active=FALSE, updated_at=now() WHERE user_id=%s AND id=%s", (int(user_id), int(reminder_id)))
    else:
        cur.execute("UPDATE public.user_reminders SET event_date=%s, updated_at=now() WHERE user_id=%s AND id=%s", (next_date, int(user_id), int(reminder_id)))
    cur.execute(
        """
        SELECT id, title, rem_type, category, amount, currency, event_date,
               repeat_rule, repeat_interval_days, notify_days_before, is_active
          FROM public.user_reminders
         WHERE user_id=%s AND id=%s
         LIMIT 1
        """,
        (int(user_id), int(reminder_id)),
    )
    updated = _row_to_reminder(cur.fetchone())
    return ReminderRecordResult("recorded", updated, recorded)


def record_reminder(
    *,
    user_id: int,
    reminder_id: int,
    workspace: WorkspaceContext,
    chat_type: str,
    expected_event_date: date | None = None,
    post_commit: bool = True,
) -> ReminderRecordResult:
    conn = get_conn()
    result: ReminderRecordResult
    try:
        with conn.cursor() as cur:
            result = record_reminder_tx(
                cur,
                user_id=user_id,
                reminder_id=reminder_id,
                workspace=workspace,
                chat_type=chat_type,
                expected_event_date=expected_event_date,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if post_commit and result.operation:
        record_financial_operation_post_commit(result.operation, workspace_kind=workspace.kind, metadata={"source": "reminder"})
    return result


def reminder_categories(reminders: Iterable[dict[str, Any]]) -> list[str]:
    return sorted({str(item.get("category") or "") for item in reminders if item.get("category")})
