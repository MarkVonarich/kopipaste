from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from psycopg2 import errors
from psycopg2.extras import Json

from db.database import get_conn
from services.analytics_privacy import pseudonymous_user_id, safe_error_code, sanitize_properties, validate_event_name
from services.event_registry import SECURITY_EVENTS

log = logging.getLogger(__name__)
SEVERITIES = {"info", "warning", "high", "critical"}


@dataclass(frozen=True)
class SecurityEvent:
    event_name: str
    severity: str = "warning"
    user_id: int | None = None
    workspace_id: int | None = None
    chat_type: str | None = None
    source: str = "telegram"
    rule_key: str | None = None
    risk_score: int | None = None
    action_taken: str = "monitor_only"
    metadata: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime | None = None


def track_security_event(ev: SecurityEvent, *, strict: bool = False) -> int | None:
    try:
        event_name = validate_event_name(ev.event_name)
        if event_name not in SECURITY_EVENTS:
            raise ValueError("unknown_security_event")
        severity = ev.severity if ev.severity in SEVERITIES else "warning"
        ident = pseudonymous_user_id(ev.user_id)
        metadata = sanitize_properties(ev.metadata)
        conn = None
        try:
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO security.security_events
                      (event_uuid, occurred_at, event_name, severity, analytics_user_id, user_id,
                       workspace_id, chat_type, source, rule_key, risk_score, action_taken, metadata)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (
                        str(uuid4()),
                        ev.occurred_at or datetime.now(timezone.utc),
                        event_name,
                        severity,
                        ident.analytics_user_id,
                        ev.user_id,
                        ev.workspace_id,
                        ev.chat_type,
                        ev.source,
                        ev.rule_key,
                        ev.risk_score,
                        ev.action_taken,
                        Json(metadata),
                    ),
                )
                event_id = int(cur.fetchone()[0])
            conn.commit()
            return event_id
        except (errors.UndefinedTable, errors.InvalidSchemaName):
            if conn is not None:
                conn.rollback()
            if strict:
                raise
            return None
        except Exception as exc:
            if conn is not None:
                conn.rollback()
            log.warning("security_event_write_failed event=%s error_code=%s", event_name, safe_error_code(exc))
            if strict:
                raise
            return None
        finally:
            if conn is not None:
                conn.close()
    except Exception:
        if strict:
            raise
        return None
