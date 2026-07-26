from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from psycopg2 import errors
from psycopg2.extras import Json

from db.database import get_conn
from services.analytics_privacy import pseudonymous_user_id, safe_error_code, sanitize_properties
from services.event_registry import API_FEATURES

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApiUsageEvent:
    provider: str
    model: str
    feature: str
    status: str
    user_id: int | None = None
    workspace_id: int | None = None
    request_count: int = 1
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: Decimal | None = None
    error_code: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime | None = None


def track_api_usage(ev: ApiUsageEvent, *, strict: bool = False) -> int | None:
    if ev.feature not in API_FEATURES:
        if strict:
            raise ValueError("unknown_api_feature")
        return None
    ident = pseudonymous_user_id(ev.user_id)
    metadata = sanitize_properties(ev.metadata)
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO analytics.api_usage_events
                  (occurred_at, provider, model, feature, status, analytics_user_id, user_id,
                   workspace_id, request_count, latency_ms, input_tokens, output_tokens,
                   estimated_cost_usd, error_code, metadata)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (
                    ev.occurred_at or datetime.now(timezone.utc),
                    ev.provider[:64],
                    ev.model[:128],
                    ev.feature,
                    ev.status[:32],
                    ident.analytics_user_id,
                    ev.user_id,
                    ev.workspace_id,
                    int(ev.request_count),
                    ev.latency_ms,
                    ev.input_tokens,
                    ev.output_tokens,
                    ev.estimated_cost_usd,
                    ev.error_code[:64] if ev.error_code else None,
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
        log.warning("api_usage_write_failed feature=%s error_code=%s", ev.feature, safe_error_code(exc))
        if strict:
            raise
        return None
    finally:
        if conn is not None:
            conn.close()
