from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

from psycopg2 import errors

from db.database import get_conn
from services.analytics_privacy import pseudonymous_user_id
from services.product_events import ProductEvent, track_product_event

log = logging.getLogger(__name__)
PAYLOAD_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}(?:__[A-Za-z0-9_-]{1,64}){0,3}$")


@dataclass(frozen=True)
class AttributionPayload:
    source: str
    campaign: str | None = None
    content: str | None = None
    referral_code: str | None = None


def parse_start_payload(payload: str | None) -> AttributionPayload | None:
    raw = (payload or "").strip()
    if not raw:
        return None
    if not PAYLOAD_RE.fullmatch(raw):
        return None
    parts = raw.split("__")
    return AttributionPayload(
        source=parts[0],
        campaign=parts[1] if len(parts) > 1 else parts[0],
        content=parts[2] if len(parts) > 2 else None,
        referral_code=parts[3] if len(parts) > 3 else None,
    )


def campaign_url(bot_username: str, payload: str) -> str:
    parsed = parse_start_payload(payload)
    if not parsed:
        raise ValueError("invalid_campaign_payload")
    username = (bot_username or "").lstrip("@").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{5,64}", username):
        raise ValueError("invalid_bot_username")
    return f"https://t.me/{username}?start={quote(payload, safe='')}"


def capture_acquisition(
    *,
    user_id: int,
    payload: str | None,
    invited_by_user_id: int | None = None,
    source: str = "telegram_start",
    strict: bool = False,
) -> bool:
    parsed = parse_start_payload(payload)
    if parsed is None:
        if payload:
            track_product_event(
                ProductEvent(
                    event_name="acquisition_payload_rejected",
                    user_id=user_id,
                    source=source,
                    status="rejected",
                    properties={"reason": "malformed_payload"},
                ),
                strict=False,
            )
        return False

    ident = pseudonymous_user_id(user_id)
    invited_ident = pseudonymous_user_id(invited_by_user_id).analytics_user_id if invited_by_user_id else None
    now = datetime.now(timezone.utc)
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO analytics.acquisition_attribution
                  (user_id, analytics_user_id, first_touch_source, first_touch_campaign,
                   first_touch_content, first_touch_referral_code, first_touch_at,
                   last_touch_source, last_touch_campaign, last_touch_content,
                   last_touch_referral_code, last_touch_at, invited_by_analytics_user_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (user_id) DO UPDATE
                   SET last_touch_source=EXCLUDED.last_touch_source,
                       last_touch_campaign=EXCLUDED.last_touch_campaign,
                       last_touch_content=EXCLUDED.last_touch_content,
                       last_touch_referral_code=EXCLUDED.last_touch_referral_code,
                       last_touch_at=EXCLUDED.last_touch_at,
                       invited_by_analytics_user_id=COALESCE(analytics.acquisition_attribution.invited_by_analytics_user_id, EXCLUDED.invited_by_analytics_user_id),
                       updated_at=now()
                """,
                (
                    user_id,
                    ident.analytics_user_id,
                    parsed.source,
                    parsed.campaign,
                    parsed.content,
                    parsed.referral_code,
                    now,
                    parsed.source,
                    parsed.campaign,
                    parsed.content,
                    parsed.referral_code,
                    now,
                    invited_ident,
                ),
            )
        conn.commit()
        return True
    except (errors.UndefinedTable, errors.InvalidSchemaName):
        if conn is not None:
            conn.rollback()
        if strict:
            raise
        return False
    except Exception:
        if conn is not None:
            conn.rollback()
        if strict:
            raise
        log.warning("acquisition_capture_failed source=%s", source)
        return False
    finally:
        if conn is not None:
            conn.close()
