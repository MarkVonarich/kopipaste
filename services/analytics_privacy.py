from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from psycopg2 import errors

from db.database import get_conn

try:
    from settings import ANALYTICS_HMAC_SECRET
except Exception:
    ANALYTICS_HMAC_SECRET = ""

log = logging.getLogger(__name__)

_warned_missing_secret = False
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_EVENT_RE = re.compile(r"^[a-z][a-z0-9_]{1,79}$")

MAX_STRING_LEN = 512
MAX_JSON_BYTES = 8192
MAX_PROPERTY_KEYS = 50

PROHIBITED_KEY_PARTS = {
    "api_key",
    "authorization",
    "callback_data",
    "comment",
    "database_url",
    "image",
    "message",
    "ocr",
    "openai",
    "phone",
    "prompt",
    "raw",
    "response",
    "secret",
    "telegram_token",
    "text",
    "token",
    "transcript",
    "username",
    "voice",
}


@dataclass(frozen=True)
class AnalyticsIdentity:
    analytics_user_id: str | None
    external_export_allowed: bool
    reason: str | None = None


def validate_event_name(event_name: str) -> str:
    name = (event_name or "").strip()
    if not _EVENT_RE.fullmatch(name):
        raise ValueError("invalid_event_name")
    return name


def validate_property_key(key: str) -> bool:
    return bool(_KEY_RE.fullmatch(key or "")) and not _is_prohibited_key(key)


def _is_prohibited_key(key: str) -> bool:
    lowered = (key or "").strip().lower()
    return any(part in lowered for part in PROHIBITED_KEY_PARTS)


def pseudonymous_user_id(user_id: int | None, secret: str | None = None) -> AnalyticsIdentity:
    global _warned_missing_secret
    if user_id is None:
        return AnalyticsIdentity(None, False, "missing_user_id")
    secret_value = (ANALYTICS_HMAC_SECRET if secret is None else secret or "").strip()
    if not secret_value:
        if not _warned_missing_secret:
            log.warning("analytics_hmac_secret_missing external_export_allowed=false")
            _warned_missing_secret = True
        return AnalyticsIdentity(None, False, "missing_secret")
    digest = hmac.new(secret_value.encode("utf-8"), str(int(user_id)).encode("utf-8"), hashlib.sha256).hexdigest()
    return AnalyticsIdentity(f"au_{digest[:40]}", True)


def _bounded_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_STRING_LEN]
    return str(value)[:MAX_STRING_LEN]


def sanitize_properties(properties: dict[str, Any] | None, *, max_json_bytes: int = MAX_JSON_BYTES) -> dict[str, Any]:
    if not properties:
        return {}
    out: dict[str, Any] = {}
    for raw_key, value in list(properties.items())[:MAX_PROPERTY_KEYS]:
        key = str(raw_key or "").strip()
        if not validate_property_key(key):
            continue
        if isinstance(value, dict):
            nested = sanitize_properties(value, max_json_bytes=max(256, max_json_bytes // 2))
            if nested:
                out[key] = nested
        elif isinstance(value, (list, tuple)):
            out[key] = [_bounded_scalar(v) for v in list(value)[:20]]
        else:
            out[key] = _bounded_scalar(value)
        while len(json.dumps(out, ensure_ascii=False, default=str).encode("utf-8")) > max_json_bytes and out:
            out.pop(next(reversed(out)))
            break
    return out


def safe_error_code(exc: Exception | str | None) -> str | None:
    if exc is None:
        return None
    if isinstance(exc, str):
        raw = exc
    else:
        raw = type(exc).__name__
    code = re.sub(r"[^a-zA-Z0-9_]+", "_", raw).strip("_").lower()
    return (code or "error")[:64]


def apply_account_deletion(user_id: int, *, recent_days: int = 180, strict: bool = False) -> dict[str, int]:
    counts = {"product_events_deleted": 0, "product_events_anonymized": 0, "outbox_suppressed": 0, "attribution_removed": 0}
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analytics.event_outbox o
                   SET status='suppressed', updated_at=now()
                  FROM analytics.product_events p
                 WHERE o.product_event_id=p.id
                   AND p.user_id=%s
                   AND o.status IN ('pending','claimed','retrying')
                """,
                (int(user_id),),
            )
            counts["outbox_suppressed"] = int(cur.rowcount)
            cur.execute(
                """
                DELETE FROM analytics.product_events
                 WHERE user_id=%s
                   AND created_at >= now() - (%s || ' days')::interval
                """,
                (int(user_id), int(recent_days)),
            )
            counts["product_events_deleted"] = int(cur.rowcount)
            cur.execute(
                """
                UPDATE analytics.product_events
                   SET user_id=NULL,
                       workspace_id=NULL,
                       entity_id=NULL,
                       external_export_allowed=FALSE,
                       anonymized_at=COALESCE(anonymized_at, now())
                 WHERE user_id=%s
                """,
                (int(user_id),),
            )
            counts["product_events_anonymized"] = int(cur.rowcount)
            cur.execute(
                """
                UPDATE analytics.acquisition_attribution
                   SET analytics_user_id=NULL,
                       invited_by_analytics_user_id=NULL,
                       deleted_at=COALESCE(deleted_at, now()),
                       updated_at=now()
                 WHERE user_id=%s
                """,
                (int(user_id),),
            )
            counts["attribution_removed"] = int(cur.rowcount)
        conn.commit()
        return counts
    except (errors.UndefinedTable, errors.InvalidSchemaName):
        if conn is not None:
            conn.rollback()
        if strict:
            raise
        return counts
    except Exception:
        if conn is not None:
            conn.rollback()
        if strict:
            raise
        log.warning("analytics_account_deletion_failed")
        return counts
    finally:
        if conn is not None:
            conn.close()


def apply_history_deletion(operation_ids: list[int], *, strict: bool = False) -> int:
    if not operation_ids:
        return 0
    entity_ids = [str(int(v)) for v in operation_ids]
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE analytics.product_events
                   SET entity_id=NULL,
                       external_export_allowed=FALSE,
                       properties = properties - 'operation_id',
                       anonymized_at=COALESCE(anonymized_at, now())
                 WHERE entity_type='operation'
                   AND entity_id = ANY(%s)
                """,
                (entity_ids,),
            )
            changed = int(cur.rowcount)
        conn.commit()
        return changed
    except (errors.UndefinedTable, errors.InvalidSchemaName):
        if conn is not None:
            conn.rollback()
        if strict:
            raise
        return 0
    except Exception:
        if conn is not None:
            conn.rollback()
        if strict:
            raise
        log.warning("analytics_history_deletion_failed")
        return 0
    finally:
        if conn is not None:
            conn.close()
