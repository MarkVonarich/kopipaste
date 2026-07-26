from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from db.database import get_conn
from settings import ANALYTICS_OUTBOX_MAX_ATTEMPTS
from services.analytics_privacy import safe_error_code


@dataclass(frozen=True)
class OutboxRow:
    id: int
    product_event_id: int
    destination: str
    attempt_count: int


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
