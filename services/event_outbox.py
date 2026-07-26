from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from db.database import get_conn
from settings import ANALYTICS_OUTBOX_MAX_ATTEMPTS
from services.analytics_privacy import safe_error_code


@dataclass(frozen=True)
class OutboxRow:
    id: int
    product_event_id: int
    destination: str
    attempt_count: int


@dataclass(frozen=True)
class ClaimedProductEvent:
    outbox_id: int
    product_event_id: int
    attempt_count: int
    event_uuid: str
    occurred_at: datetime
    event_name: str
    event_version: int
    event_group: str
    analytics_user_id: str | None
    workspace_kind: str | None
    source: str | None
    platform: str | None
    locale: str | None
    currency: str | None
    status: str | None
    duration_ms: int | None
    entity_type: str | None
    entity_id: str | None
    properties: dict[str, Any]
    external_export_allowed: bool
    deleted_at: datetime | None


def retry_delay_seconds(attempt_count: int) -> int:
    return min(3600, 60 * (2 ** max(0, int(attempt_count) - 1)))


def next_retry_at(attempt_count: int, now: datetime | None = None) -> datetime:
    base = now or datetime.now(timezone.utc)
    return base + timedelta(seconds=retry_delay_seconds(attempt_count))


def status_after_failure(attempt_count: int, max_attempts: int = ANALYTICS_OUTBOX_MAX_ATTEMPTS) -> str:
    return "dead_letter" if int(attempt_count) >= int(max_attempts) else "retrying"


def claim_outbox_batch(*, destination: str = "posthog", limit: int = 100, locked_by: str = "worker") -> list[OutboxRow]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH claimed AS (
                    SELECT id
                      FROM analytics.event_outbox
                     WHERE destination=%s
                       AND status IN ('pending','retrying')
                       AND next_attempt_at <= now()
                     ORDER BY next_attempt_at, id
                     LIMIT %s
                     FOR UPDATE SKIP LOCKED
                )
                UPDATE analytics.event_outbox o
                   SET status='claimed',
                       locked_at=now(),
                       locked_by=%s,
                       updated_at=now()
                  FROM claimed
                 WHERE o.id=claimed.id
                 RETURNING o.id, o.product_event_id, o.destination, o.attempt_count
                """,
                (destination, int(limit), locked_by[:128]),
            )
            rows = [OutboxRow(int(r[0]), int(r[1]), r[2], int(r[3])) for r in cur.fetchall()]
        conn.commit()
        return rows
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def release_stale_claims(*, destination: str = "posthog", older_than_seconds: int = 900) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analytics.event_outbox
                   SET status='retrying',
                       locked_at=NULL,
                       locked_by=NULL,
                       next_attempt_at=LEAST(next_attempt_at, now()),
                       updated_at=now()
                 WHERE destination=%s
                   AND status='claimed'
                   AND locked_at < now() - (%s || ' seconds')::interval
                """,
                (destination, int(older_than_seconds)),
            )
            changed = int(cur.rowcount)
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def claim_posthog_product_events(
    *,
    limit: int = 100,
    locked_by: str = "worker",
    max_event_age_days: int = 30,
    event_uuid: str | None = None,
) -> list[ClaimedProductEvent]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            filters = [
                "o.destination='posthog'",
                "o.status IN ('pending','retrying')",
                "o.next_attempt_at <= now()",
                "p.deleted_at IS NULL",
                "p.occurred_at >= now() - (%s || ' days')::interval",
            ]
            params: list[Any] = [int(max_event_age_days)]
            if event_uuid:
                filters.append("p.event_uuid=%s")
                params.append(str(event_uuid))
            params.extend([int(limit), locked_by[:128]])
            cur.execute(
                f"""
                WITH eligible AS (
                    SELECT o.id
                      FROM analytics.event_outbox o
                      JOIN analytics.product_events p ON p.id=o.product_event_id
                     WHERE {' AND '.join(filters)}
                     ORDER BY o.next_attempt_at, o.id
                     LIMIT %s
                     FOR UPDATE SKIP LOCKED
                )
                UPDATE analytics.event_outbox o
                   SET status='claimed',
                       locked_at=now(),
                       locked_by=%s,
                       updated_at=now()
                  FROM eligible
                 WHERE o.id=eligible.id
                 RETURNING
                    o.id, o.product_event_id, o.attempt_count,
                    (SELECT p.event_uuid FROM analytics.product_events p WHERE p.id=o.product_event_id),
                    (SELECT p.occurred_at FROM analytics.product_events p WHERE p.id=o.product_event_id),
                    (SELECT p.event_name FROM analytics.product_events p WHERE p.id=o.product_event_id),
                    (SELECT p.event_version FROM analytics.product_events p WHERE p.id=o.product_event_id),
                    (SELECT p.event_group FROM analytics.product_events p WHERE p.id=o.product_event_id),
                    (SELECT p.analytics_user_id FROM analytics.product_events p WHERE p.id=o.product_event_id),
                    (SELECT p.workspace_kind FROM analytics.product_events p WHERE p.id=o.product_event_id),
                    (SELECT p.source FROM analytics.product_events p WHERE p.id=o.product_event_id),
                    (SELECT p.platform FROM analytics.product_events p WHERE p.id=o.product_event_id),
                    (SELECT p.locale FROM analytics.product_events p WHERE p.id=o.product_event_id),
                    (SELECT p.currency FROM analytics.product_events p WHERE p.id=o.product_event_id),
                    (SELECT p.status FROM analytics.product_events p WHERE p.id=o.product_event_id),
                    (SELECT p.duration_ms FROM analytics.product_events p WHERE p.id=o.product_event_id),
                    (SELECT p.entity_type FROM analytics.product_events p WHERE p.id=o.product_event_id),
                    (SELECT p.entity_id FROM analytics.product_events p WHERE p.id=o.product_event_id),
                    (SELECT p.properties FROM analytics.product_events p WHERE p.id=o.product_event_id),
                    (SELECT p.external_export_allowed FROM analytics.product_events p WHERE p.id=o.product_event_id),
                    (SELECT p.deleted_at FROM analytics.product_events p WHERE p.id=o.product_event_id)
                """,
                tuple(params),
            )
            rows = [
                ClaimedProductEvent(
                    outbox_id=int(r[0]),
                    product_event_id=int(r[1]),
                    attempt_count=int(r[2]),
                    event_uuid=str(r[3]),
                    occurred_at=r[4],
                    event_name=r[5],
                    event_version=int(r[6]),
                    event_group=r[7],
                    analytics_user_id=r[8],
                    workspace_kind=r[9],
                    source=r[10],
                    platform=r[11],
                    locale=r[12],
                    currency=r[13],
                    status=r[14],
                    duration_ms=r[15],
                    entity_type=r[16],
                    entity_id=r[17],
                    properties=r[18] or {},
                    external_export_allowed=bool(r[19]),
                    deleted_at=r[20],
                )
                for r in cur.fetchall()
            ]
        conn.commit()
        return rows
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def suppress_outbox_row(outbox_id: int, reason: str = "suppressed") -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analytics.event_outbox
                   SET status='suppressed',
                       locked_at=NULL,
                       locked_by=NULL,
                       last_error_code=%s,
                       updated_at=now()
                 WHERE id=%s AND status <> 'sent'
                """,
                (safe_error_code(reason), int(outbox_id)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def release_outbox_claim(outbox_id: int, reason: str = "released") -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analytics.event_outbox
                   SET status='retrying',
                       locked_at=NULL,
                       locked_by=NULL,
                       next_attempt_at=now(),
                       last_error_code=%s,
                       updated_at=now()
                 WHERE id=%s AND status='claimed'
                """,
                (safe_error_code(reason), int(outbox_id)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def suppress_unexportable_posthog_rows(*, max_event_age_days: int = 30) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analytics.event_outbox o
                   SET status='suppressed',
                       locked_at=NULL,
                       locked_by=NULL,
                       last_error_code=CASE
                         WHEN p.deleted_at IS NOT NULL THEN 'deleted_user'
                         WHEN p.external_export_allowed IS FALSE THEN 'export_disallowed'
                         WHEN p.analytics_user_id IS NULL THEN 'missing_distinct_id'
                         WHEN p.occurred_at < now() - (%s || ' days')::interval THEN 'event_too_old'
                         ELSE 'suppressed'
                       END,
                       updated_at=now()
                  FROM analytics.product_events p
                 WHERE o.product_event_id=p.id
                   AND o.destination='posthog'
                   AND o.status IN ('pending','retrying','claimed')
                   AND (
                        p.deleted_at IS NOT NULL
                        OR p.external_export_allowed IS FALSE
                        OR p.analytics_user_id IS NULL
                        OR p.occurred_at < now() - (%s || ' days')::interval
                   )
                """,
                (int(max_event_age_days), int(max_event_age_days)),
            )
            changed = int(cur.rowcount)
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_outbox_sent(outbox_id: int) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analytics.event_outbox
                   SET status='sent', sent_at=COALESCE(sent_at, now()), locked_at=NULL,
                       locked_by=NULL, updated_at=now()
                 WHERE id=%s AND status <> 'sent'
                """,
                (int(outbox_id),),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_outbox_failed(outbox_id: int, error: Exception | str | None, *, max_attempts: int = ANALYTICS_OUTBOX_MAX_ATTEMPTS) -> str:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT attempt_count
                  FROM analytics.event_outbox
                 WHERE id=%s
                 FOR UPDATE
                """,
                (int(outbox_id),),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return "not_found"
            attempts = int(row[0]) + 1
            status = status_after_failure(attempts, max_attempts=max_attempts)
            cur.execute(
                """
                UPDATE analytics.event_outbox
                   SET status=%s, attempt_count=%s, next_attempt_at=%s,
                       locked_at=NULL, locked_by=NULL, last_error_code=%s, updated_at=now()
                 WHERE id=%s
                """,
                (status, attempts, next_retry_at(attempts), safe_error_code(error), int(outbox_id)),
            )
        conn.commit()
        return status
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
