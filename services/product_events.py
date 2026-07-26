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
from services.event_registry import PRODUCT_EVENT_GROUPS

log = logging.getLogger(__name__)
_failed_local_writes = 0


@dataclass(frozen=True)
class ProductEvent:
    event_name: str
    user_id: int | None = None
    workspace_id: int | None = None
    workspace_kind: str | None = None
    session_id: str | None = None
    source: str = "telegram"
    platform: str = "telegram"
    locale: str | None = None
    currency: str | None = None
    status: str | None = None
    duration_ms: int | None = None
    entity_type: str | None = None
    entity_id: str | int | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    event_version: int = 1
    occurred_at: datetime | None = None
    destination: str = "posthog"


def failed_local_write_count() -> int:
    return _failed_local_writes


def _normalize_event(ev: ProductEvent) -> tuple[ProductEvent, dict, Any]:
    event_name = validate_event_name(ev.event_name)
    if event_name not in PRODUCT_EVENT_GROUPS:
        raise ValueError("unknown_product_event")
    ident = pseudonymous_user_id(ev.user_id)
    props = sanitize_properties(ev.properties)
    return ev, props, ident


def insert_product_event_cur(cur, ev: ProductEvent, *, create_outbox: bool = True) -> int:
    _, props, ident = _normalize_event(ev)
    event_uuid = str(uuid4())
    occurred_at = ev.occurred_at or datetime.now(timezone.utc)
    cur.execute(
        """
        INSERT INTO analytics.product_events
          (event_uuid, occurred_at, event_name, event_version, event_group,
           analytics_user_id, user_id, workspace_id, workspace_kind, session_id,
           source, platform, locale, currency, status, duration_ms, entity_type,
           entity_id, properties, external_export_allowed)
        VALUES
          (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
        """,
        (
            event_uuid,
            occurred_at,
            ev.event_name,
            int(ev.event_version),
            PRODUCT_EVENT_GROUPS[ev.event_name],
            ident.analytics_user_id,
            ev.user_id,
            ev.workspace_id,
            ev.workspace_kind,
            ev.session_id,
            ev.source,
            ev.platform,
            ev.locale,
            ev.currency,
            ev.status,
            ev.duration_ms,
            ev.entity_type,
            str(ev.entity_id)[:128] if ev.entity_id is not None else None,
            Json(props),
            bool(ident.external_export_allowed),
        ),
    )
    product_event_id = int(cur.fetchone()[0])
    if create_outbox and ident.external_export_allowed:
        cur.execute(
            """
            INSERT INTO analytics.event_outbox (product_event_id, destination, status, next_attempt_at)
            VALUES (%s, %s, 'pending', now())
            """,
            (product_event_id, ev.destination),
        )
    return product_event_id


def track_product_event(ev: ProductEvent, *, strict: bool = False) -> int | None:
    global _failed_local_writes
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            event_id = insert_product_event_cur(cur, ev, create_outbox=True)
        conn.commit()
        return event_id
    except (errors.UndefinedTable, errors.InvalidSchemaName):
        if conn is not None:
            conn.rollback()
        _failed_local_writes += 1
        if strict:
            raise
        return None
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        _failed_local_writes += 1
        log.warning("product_event_write_failed event=%s error_code=%s", ev.event_name, safe_error_code(exc))
        if strict:
            raise
        return None
    finally:
        if conn is not None:
            conn.close()
