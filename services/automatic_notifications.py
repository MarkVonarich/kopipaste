from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from psycopg2 import errors
from psycopg2.extras import Json
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden, TimedOut
from telegram.ext import ContextTypes

from db.database import get_conn, pg_fetchall
from services.analytics_privacy import safe_error_code
from services.product_events import ProductEvent, track_product_event
from services.user_time import is_local_time_in_window, resolve_user_timezone, user_timezone_name

log = logging.getLogger(__name__)


class DeliveryPolicy(StrEnum):
    DEFER = "defer"
    SKIP = "skip"


@dataclass(frozen=True)
class QuietHoursWindow:
    enabled: bool
    start: time | None
    end: time | None
    timezone_name: str
    fallback_reason: str | None = None


@dataclass(frozen=True)
class DispatchResult:
    status: str
    deferred_until: datetime | None = None
    notification_id: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class NotificationInsertResult:
    notification_id: int
    created: bool


def parse_hhmm(value: str | None) -> time | None:
    if not value:
        return None
    hour_s, minute_s = str(value).split(":", 1)
    return time(int(hour_s), int(minute_s))


def quiet_hours_window(user_id: int) -> QuietHoursWindow:
    resolved = resolve_user_timezone(user_id)
    tz_name = resolved.timezone_name
    fallback_reason = resolved.fallback_reason
    try:
        rows = pg_fetchall(
            """
            SELECT COALESCE(quiet_hours_enabled, false),
                   to_char(quiet_hours_start, 'HH24:MI'),
                   to_char(quiet_hours_end, 'HH24:MI')
              FROM public.notification_preferences
             WHERE user_id=%s
             LIMIT 1
            """,
            (user_id,),
        )
    except errors.UndefinedColumn:
        try:
            rows = pg_fetchall(
                """
                SELECT quiet_hours_start IS NOT NULL AND quiet_hours_end IS NOT NULL,
                       to_char(quiet_hours_start, 'HH24:MI'),
                       to_char(quiet_hours_end, 'HH24:MI')
                  FROM public.notification_preferences
                 WHERE user_id=%s
                 LIMIT 1
                """,
                (user_id,),
            )
        except (errors.UndefinedTable, errors.UndefinedColumn):
            rows = []
    except (errors.UndefinedTable, errors.UndefinedColumn):
        rows = []
    if not rows or not rows[0][0] or not rows[0][1] or not rows[0][2]:
        return QuietHoursWindow(False, None, None, tz_name, fallback_reason)
    return QuietHoursWindow(True, parse_hhmm(rows[0][1]), parse_hhmm(rows[0][2]), tz_name, fallback_reason)


def is_quiet_local(local_dt: datetime, start: time, end: time) -> bool:
    return is_local_time_in_window(local_dt, start, end)


def next_quiet_end_utc(now_utc: datetime, window: QuietHoursWindow) -> datetime:
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    tz = ZoneInfo(window.timezone_name)
    local_now = now_utc.astimezone(tz)
    if not window.enabled or window.start is None or window.end is None:
        return now_utc
    end_local = datetime.combine(local_now.date(), window.end, tzinfo=tz)
    if window.start > window.end and local_now.time().replace(second=0, microsecond=0) >= window.start:
        end_local += timedelta(days=1)
    if end_local <= local_now:
        end_local += timedelta(days=1)
    return end_local.astimezone(timezone.utc)


def quiet_context(user_id: int, *, now_utc: datetime | None = None) -> tuple[QuietHoursWindow, datetime, bool]:
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    window = quiet_hours_window(user_id)
    if not window.enabled or window.start is None or window.end is None:
        return window, now, False
    local_now = now.astimezone(ZoneInfo(window.timezone_name))
    return window, now, is_quiet_local(local_now, window.start, window.end)


def suppress_stale_timezone_sensitive_notifications(user_id: int, *, reason: str = "timezone_changed_stale_notification") -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.automatic_notifications
                   SET status='skipped',
                       skip_reason=%s,
                       locked_at=NULL,
                       locked_by=NULL,
                       updated_at=now()
                 WHERE user_id=%s
                   AND status IN ('pending','claimed')
                   AND notification_type IN (
                       'day_nudge',
                       'evening_reminder',
                       'smart_morning_limit',
                       'weekly_report',
                       'monthly_report',
                       'user_reminder',
                       'challenge_prompt',
                       'goal_planned_contribution'
                   )
                """,
                (reason, int(user_id)),
            )
            changed = int(cur.rowcount or 0)
        conn.commit()
        return changed
    except (errors.UndefinedTable, errors.UndefinedColumn):
        conn.rollback()
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _insert_deferred(
    *,
    user_id: int,
    workspace_id: int | None,
    notification_type: str,
    dedupe_key: str,
    template_key: str,
    payload: dict[str, Any],
    original_scheduled_at: datetime,
    earliest_delivery_at: datetime,
    timezone_name: str,
    policy: DeliveryPolicy,
) -> NotificationInsertResult | None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.automatic_notifications
                    (user_id, workspace_id, notification_type, dedupe_key, template_key,
                     payload, delivery_policy, original_scheduled_at, earliest_delivery_at,
                     timezone_name, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')
                ON CONFLICT (user_id, notification_type, dedupe_key) DO NOTHING
                RETURNING id
                """,
                (
                    user_id,
                    workspace_id,
                    notification_type,
                    dedupe_key,
                    template_key,
                    Json(payload),
                    policy.value,
                    original_scheduled_at,
                    earliest_delivery_at,
                    timezone_name,
                ),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return NotificationInsertResult(int(row[0]), True)
            cur.execute(
                """
                UPDATE public.automatic_notifications
                   SET earliest_delivery_at=LEAST(public.automatic_notifications.earliest_delivery_at, %s),
                       updated_at=now()
                 WHERE user_id=%s AND notification_type=%s AND dedupe_key=%s
                   AND status IN ('pending','claimed')
                RETURNING id
                """,
                (earliest_delivery_at, user_id, notification_type, dedupe_key),
            )
            row = cur.fetchone()
        conn.commit()
        return NotificationInsertResult(int(row[0]), False) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _claim_immediate_send(
    *,
    user_id: int,
    workspace_id: int | None,
    notification_type: str,
    dedupe_key: str,
    template_key: str,
    payload: dict[str, Any],
    original_scheduled_at: datetime,
    timezone_name: str,
    policy: DeliveryPolicy,
) -> int | None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.automatic_notifications
                    (user_id, workspace_id, notification_type, dedupe_key, template_key,
                     payload, delivery_policy, original_scheduled_at, earliest_delivery_at,
                     timezone_name, status, locked_at, locked_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'claimed',now(),%s)
                ON CONFLICT (user_id, notification_type, dedupe_key) DO NOTHING
                RETURNING id
                """,
                (
                    user_id,
                    workspace_id,
                    notification_type,
                    dedupe_key,
                    template_key,
                    Json(payload),
                    policy.value,
                    original_scheduled_at,
                    original_scheduled_at,
                    timezone_name,
                    f"immediate-{socket.gethostname()}"[:128],
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return int(row[0]) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _mark_skip(*, user_id: int, notification_type: str, dedupe_key: str, reason: str) -> bool:
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.automatic_notifications
                        (user_id, notification_type, dedupe_key, template_key, delivery_policy,
                         original_scheduled_at, earliest_delivery_at, status, skip_reason)
                    VALUES (%s,%s,%s,%s,'skip',now(),now(),'skipped',%s)
                    ON CONFLICT (user_id, notification_type, dedupe_key) DO NOTHING
                    RETURNING id
                    """,
                    (user_id, notification_type, dedupe_key, notification_type, reason),
                )
                created = cur.fetchone() is not None
            conn.commit()
            return created
        finally:
            conn.close()
    except Exception:
        log.info("automatic_notification_skip_record_failed type=%s reason=%s", notification_type, safe_error_code(reason))
        return False


def queue_automatic_notification(
    *,
    user_id: int,
    workspace_id: int | None = None,
    notification_type: str,
    dedupe_key: str,
    policy: DeliveryPolicy,
    template_key: str | None = None,
    payload: dict[str, Any] | None = None,
    original_scheduled_at: datetime | None = None,
) -> DispatchResult:
    window, now_utc, quiet = quiet_context(user_id, now_utc=original_scheduled_at)
    if quiet and policy == DeliveryPolicy.SKIP:
        created = _mark_skip(user_id=user_id, notification_type=notification_type, dedupe_key=dedupe_key, reason="quiet_hours")
        if created is False:
            return DispatchResult("duplicate", reason="dedupe")
        return DispatchResult("skipped", reason="quiet_hours")
    due = next_quiet_end_utc(now_utc, window) if quiet and policy == DeliveryPolicy.DEFER else now_utc
    insert_result = _insert_deferred(
        user_id=user_id,
        workspace_id=workspace_id,
        notification_type=notification_type,
        dedupe_key=dedupe_key,
        template_key=template_key or notification_type,
        payload=payload or {},
        original_scheduled_at=original_scheduled_at or now_utc,
        earliest_delivery_at=due,
        timezone_name=window.timezone_name,
        policy=policy,
    )
    if insert_result is None:
        return DispatchResult("duplicate", reason="dedupe")
    notification_id = getattr(insert_result, "notification_id", insert_result)
    created = bool(getattr(insert_result, "created", True))
    return DispatchResult("queued" if created else "deferred", deferred_until=due, notification_id=int(notification_id), reason="quiet_hours" if quiet else None)


def suppress_unsent_challenge_notifications(*, reason: str = "challenge_notifications_default_off_rollout") -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.automatic_notifications
                   SET status='skipped',
                       skip_reason=%s,
                       locked_at=NULL,
                       locked_by=NULL,
                       updated_at=now()
                 WHERE notification_type IN ('challenge_prompt','challenge_completed','achievement_granted')
                   AND status IN ('pending','claimed')
                """,
                (reason,),
            )
            changed = int(cur.rowcount or 0)
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def dispatch_automatic_notification(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    chat_id: int | None = None,
    workspace_id: int | None = None,
    notification_type: str,
    dedupe_key: str,
    policy: DeliveryPolicy,
    text: str,
    reply_markup=None,
    parse_mode: str | None = None,
    template_key: str | None = None,
    payload: dict[str, Any] | None = None,
    original_scheduled_at: datetime | None = None,
    disable_web_page_preview: bool | None = None,
    force_immediate: bool = False,
) -> DispatchResult:
    window, now_utc, quiet = quiet_context(user_id, now_utc=original_scheduled_at)
    if force_immediate:
        quiet = False
    if quiet and policy == DeliveryPolicy.SKIP:
        created = _mark_skip(user_id=user_id, notification_type=notification_type, dedupe_key=dedupe_key, reason="quiet_hours")
        if created is False:
            return DispatchResult("duplicate", reason="dedupe")
        track_product_event(ProductEvent(
            event_name="automatic_notification_skipped_quiet_hours",
            user_id=user_id,
            workspace_id=workspace_id,
            status="skipped",
            properties={"notification_type": notification_type, "policy": policy.value, "reason": "quiet_hours"},
        ))
        return DispatchResult("skipped", reason="quiet_hours")
    if quiet and policy == DeliveryPolicy.DEFER:
        due = next_quiet_end_utc(now_utc, window)
        insert_result = _insert_deferred(
            user_id=user_id,
            workspace_id=workspace_id,
            notification_type=notification_type,
            dedupe_key=dedupe_key,
            template_key=template_key or notification_type,
            payload=payload or {},
            original_scheduled_at=original_scheduled_at or now_utc,
            earliest_delivery_at=due,
            timezone_name=window.timezone_name,
            policy=policy,
        )
        if insert_result is None:
            return DispatchResult("duplicate", reason="dedupe")
        notification_id = getattr(insert_result, "notification_id", insert_result)
        created = bool(getattr(insert_result, "created", True))
        if created:
            track_product_event(ProductEvent(
                event_name="automatic_notification_deferred",
                user_id=user_id,
                workspace_id=workspace_id,
                status="deferred",
                properties={"notification_type": notification_type, "policy": policy.value},
            ))
        return DispatchResult("deferred", deferred_until=due, notification_id=int(notification_id), reason="quiet_hours")
    notification_id = _claim_immediate_send(
        user_id=user_id,
        workspace_id=workspace_id,
        notification_type=notification_type,
        dedupe_key=dedupe_key,
        template_key=template_key or notification_type,
        payload=payload or {},
        original_scheduled_at=original_scheduled_at or now_utc,
        timezone_name=window.timezone_name,
        policy=policy,
    )
    if notification_id is None:
        return DispatchResult("duplicate", reason="dedupe")
    try:
        await context.bot.send_message(
            chat_id=chat_id or user_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
        mark_notification_sent(notification_id)
        track_product_event(ProductEvent(
            event_name="automatic_notification_sent",
            user_id=user_id,
            workspace_id=workspace_id,
            status="sent",
            properties={"notification_type": notification_type, "policy": policy.value},
        ))
        return DispatchResult("sent", notification_id=notification_id)
    except (Forbidden, BadRequest) as exc:
        mark_notification_skipped(notification_id, exc)
        log.info("automatic_notification_send_skipped type=%s reason=%s", notification_type, safe_error_code(exc))
        return DispatchResult("skipped", reason=safe_error_code(exc))


def release_stale_deferred_claims(*, older_than_seconds: int = 900) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.automatic_notifications
                   SET status='pending', locked_at=NULL, locked_by=NULL, updated_at=now()
                 WHERE status='claimed'
                   AND locked_at < now() - (%s || ' seconds')::interval
                """,
                (int(older_than_seconds),),
            )
            changed = int(cur.rowcount or 0)
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def claim_due_notifications(*, limit: int = 50, locked_by: str | None = None) -> list[dict[str, Any]]:
    worker = (locked_by or f"automatic-{socket.gethostname()}")[:128]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH due AS (
                    SELECT id
                      FROM public.automatic_notifications
                     WHERE status='pending'
                       AND earliest_delivery_at <= now()
                     ORDER BY earliest_delivery_at, id
                     LIMIT %s
                     FOR UPDATE SKIP LOCKED
                )
                UPDATE public.automatic_notifications n
                   SET status='claimed', locked_at=now(), locked_by=%s, updated_at=now()
                  FROM due
                 WHERE n.id=due.id
                 RETURNING n.id, n.user_id, n.workspace_id, n.notification_type,
                           n.dedupe_key, n.template_key, n.payload,
                           n.original_scheduled_at, n.timezone_name, n.attempts
                """,
                (int(limit), worker),
            )
            rows = cur.fetchall()
        conn.commit()
        return [
            {
                "id": int(r[0]),
                "user_id": int(r[1]),
                "workspace_id": r[2],
                "notification_type": r[3],
                "dedupe_key": r[4],
                "template_key": r[5],
                "payload": r[6] or {},
                "original_scheduled_at": r[7],
                "timezone_name": r[8],
                "attempts": int(r[9] or 0),
            }
            for r in rows
        ]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_notification_sent(notification_id: int) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.automatic_notifications
                   SET status='sent', sent_at=COALESCE(sent_at, now()),
                       locked_at=NULL, locked_by=NULL, updated_at=now()
                 WHERE id=%s AND status <> 'sent'
                """,
                (int(notification_id),),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_notification_skipped(notification_id: int, reason: Exception | str) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.automatic_notifications
                   SET status='skipped',
                       skip_reason=%s,
                       locked_at=NULL,
                       locked_by=NULL,
                       updated_at=now()
                 WHERE id=%s AND status <> 'sent'
                """,
                (safe_error_code(reason), int(notification_id)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_notification_failed(notification_id: int, error: Exception | str) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.automatic_notifications
                   SET attempts=attempts + 1,
                       status=CASE WHEN attempts + 1 >= 5 THEN 'dead_letter' ELSE 'pending' END,
                       earliest_delivery_at=now() + ((60 * power(2, LEAST(attempts, 5))) || ' seconds')::interval,
                       locked_at=NULL,
                       locked_by=NULL,
                       last_error_code=%s,
                       updated_at=now()
                 WHERE id=%s
                """,
                (safe_error_code(error), int(notification_id)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _markup_from_payload(payload: dict[str, Any] | None) -> InlineKeyboardMarkup | None:
    rows = []
    for row in (payload or {}).get("buttons") or []:
        buttons = []
        for item in row:
            label = str(item.get("label") or "")[:48]
            callback_data = str(item.get("callback_data") or "")[:64]
            if label and callback_data:
                buttons.append(InlineKeyboardButton(label, callback_data=callback_data))
        if buttons:
            rows.append(buttons)
    return InlineKeyboardMarkup(rows) if rows else None


def render_deferred_notification(row: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup | None]:
    payload = row.get("payload") or {}
    key = row.get("template_key")
    if key == "weekly_report":
        from jobs.daily import build_weekly_report_text, weekly_report_kb

        start = datetime.fromisoformat(payload["start"]).date()
        end = datetime.fromisoformat(payload["end"]).date()
        return f"{build_weekly_report_text(row['user_id'], start, end)}\n\n📥 Хотите сохранить подробные данные за эту неделю?", weekly_report_kb(start, end)
    if key == "monthly_report":
        from jobs.daily import build_monthly_report_text, monthly_report_kb

        start = datetime.fromisoformat(payload["start"]).date()
        end = datetime.fromisoformat(payload["end"]).date()
        return f"{build_monthly_report_text(row['user_id'], start, end)}\n\n📥 Подробную выгрузку за месяц можно получить здесь:", monthly_report_kb(start, end)
    if key == "user_reminder":
        from jobs.daily import build_user_reminder_message

        return build_user_reminder_message(
            reminder_id=int(payload["reminder_id"]),
            event_date=datetime.fromisoformat(payload["event_date"]).date(),
            notify_days_before=int(payload["notify_days_before"]),
            delayed=True,
        )
    if key == "smart_morning_limit":
        from jobs.daily import _build_smart_morning_text

        text = _build_smart_morning_text(row["user_id"], datetime.fromisoformat(payload["local_date"]).date())
        if not text:
            raise ValueError("stale_notification")
        return text, None
    text = str(payload.get("text") or "")
    if not text:
        raise ValueError("missing_text")
    return text, _markup_from_payload(payload)


async def process_due_notifications(context: ContextTypes.DEFAULT_TYPE, *, limit: int = 50) -> dict[str, int]:
    release_stale_deferred_claims()
    rows = claim_due_notifications(limit=limit)
    counts = {"claimed": len(rows), "sent": 0, "retrying": 0, "dead_letter": 0, "skipped": 0}
    for row in rows:
        try:
            text, markup = render_deferred_notification(row)
            parse_mode = (row.get("payload") or {}).get("parse_mode")
            await context.bot.send_message(chat_id=row["user_id"], text=text, reply_markup=markup, parse_mode=parse_mode)
            mark_notification_sent(row["id"])
            counts["sent"] += 1
            track_product_event(ProductEvent(
                event_name="automatic_notification_sent",
                user_id=row["user_id"],
                workspace_id=row.get("workspace_id"),
                status="sent",
                properties={"notification_type": row["notification_type"], "deferred": True},
            ))
            if row["notification_type"] in {"challenge_prompt", "challenge_completed", "achievement_granted"}:
                track_product_event(ProductEvent(
                    event_name="challenge_notification_sent",
                    user_id=row["user_id"],
                    workspace_id=row.get("workspace_id"),
                    status="sent",
                    properties={"notification_type": row["notification_type"], "deferred": True},
                ))
        except ValueError as exc:
            mark_notification_failed(row["id"], exc)
            counts["skipped"] += 1
        except (Forbidden, BadRequest, TimedOut) as exc:
            mark_notification_failed(row["id"], exc)
            counts["retrying"] += 1
        except Exception as exc:
            log.warning("automatic_notification_due_failed type=%s reason=%s", row.get("notification_type"), safe_error_code(exc))
            mark_notification_failed(row["id"], exc)
            counts["retrying"] += 1
    return counts
