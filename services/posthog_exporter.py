from __future__ import annotations

import logging
import json
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests

from db.database import pg_fetchall
from services.analytics_privacy import sanitize_properties, validate_event_name
from services.event_outbox import (
    ClaimedProductEvent,
    claim_posthog_product_events,
    mark_outbox_failed,
    mark_outbox_sent,
    release_stale_claims,
    release_outbox_claim,
    suppress_unexportable_posthog_rows,
)
from settings import (
    POSTHOG_EXPORT_BATCH_SIZE,
    POSTHOG_EXPORT_ENABLED,
    POSTHOG_EXPORT_MAX_ATTEMPTS,
    POSTHOG_EXPORT_MAX_EVENT_AGE_DAYS,
    POSTHOG_EXPORT_TIMEOUT_SECONDS,
    POSTHOG_HOST,
    POSTHOG_PROJECT_TOKEN,
)

log = logging.getLogger(__name__)

PERMANENT_ERROR_CODES = {"disabled", "config_missing", "invalid_host", "malformed_event", "http_400", "http_401", "http_403", "http_413"}
RETRYABLE_ERROR_CODES = {"timeout", "connection_error", "http_429", "http_5xx", "unexpected_error"}
MAX_BATCH_ITEM_BYTES = 32 * 1024


@dataclass(frozen=True)
class PostHogConfig:
    enabled: bool
    project_token: str
    host: str
    batch_size: int
    timeout_seconds: int
    max_attempts: int
    max_event_age_days: int
    error_code: str | None = None

    @property
    def batch_url(self) -> str:
        return f"{self.host}/batch/"

    @property
    def can_send(self) -> bool:
        return self.enabled and not self.error_code


@dataclass(frozen=True)
class ExportSummary:
    claimed: int = 0
    sent: int = 0
    retried: int = 0
    dead_letter: int = 0
    skipped: int = 0
    duration_ms: int = 0
    error_code: str | None = None


_RUNNING = False


def load_posthog_config() -> PostHogConfig:
    host = _normalize_host(POSTHOG_HOST)
    token = POSTHOG_PROJECT_TOKEN
    error = None
    if not POSTHOG_EXPORT_ENABLED:
        error = "disabled"
    elif not token or not POSTHOG_HOST:
        error = "config_missing"
    elif host is None:
        error = "invalid_host"
    return PostHogConfig(
        enabled=bool(POSTHOG_EXPORT_ENABLED),
        project_token=token,
        host=host or "",
        batch_size=max(1, min(500, int(POSTHOG_EXPORT_BATCH_SIZE))),
        timeout_seconds=max(1, int(POSTHOG_EXPORT_TIMEOUT_SECONDS)),
        max_attempts=max(1, int(POSTHOG_EXPORT_MAX_ATTEMPTS)),
        max_event_age_days=max(1, int(POSTHOG_EXPORT_MAX_EVENT_AGE_DAYS)),
        error_code=error,
    )


def _normalize_host(raw_host: str | None) -> str | None:
    raw = (raw_host or "").strip().rstrip("/")
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    if parsed.params or parsed.query or parsed.fragment:
        return None
    if parsed.path not in {"", "/"}:
        return None
    return f"https://{parsed.netloc}"


def classify_http_status(status_code: int) -> str:
    if status_code == 400:
        return "http_400"
    if status_code == 401:
        return "http_401"
    if status_code == 403:
        return "http_403"
    if status_code == 413:
        return "http_413"
    if status_code == 429:
        return "http_429"
    if 500 <= status_code <= 599:
        return "http_5xx"
    return f"http_{status_code}"


def posthog_properties(event: ClaimedProductEvent) -> dict[str, Any]:
    if not event.external_export_allowed or event.deleted_at is not None:
        raise ValueError("suppressed")
    if not event.analytics_user_id:
        raise ValueError("missing_distinct_id")
    validate_event_name(event.event_name)
    if len(json.dumps(event.properties or {}, ensure_ascii=False, default=str).encode("utf-8")) > MAX_BATCH_ITEM_BYTES:
        raise ValueError("malformed_event")
    safe_event_props = sanitize_properties(event.properties)
    props: dict[str, Any] = dict(safe_event_props)
    props.update({
        "distinct_id": event.analytics_user_id,
        "event_uuid": event.event_uuid,
        "event_version": event.event_version,
        "event_group": event.event_group,
        "source": event.source or "telegram",
        "platform": event.platform or "telegram",
    })
    optional = {
        "status": event.status,
        "workspace_kind": event.workspace_kind,
        "locale": event.locale,
        "currency": event.currency,
        "duration_ms": event.duration_ms,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
    }
    props.update({k: v for k, v in optional.items() if v is not None})
    return sanitize_properties(props, max_json_bytes=MAX_BATCH_ITEM_BYTES)


def posthog_batch_item(event: ClaimedProductEvent) -> dict[str, Any]:
    props = posthog_properties(event)
    if not props.get("distinct_id") or not props.get("event_uuid"):
        raise ValueError("malformed_event")
    timestamp = event.occurred_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    item = {
        "event": event.event_name,
        "timestamp": timestamp.isoformat(),
        "properties": props,
    }
    if len(json.dumps(item, ensure_ascii=False, default=str).encode("utf-8")) > MAX_BATCH_ITEM_BYTES:
        raise ValueError("malformed_event")
    return item


def build_posthog_body(config: PostHogConfig, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "api_key": config.project_token,
        "historical_migration": False,
        "batch": items,
    }


def _mark_failure(outbox_id: int, error_code: str, *, config: PostHogConfig) -> str:
    max_attempts = 1 if error_code in PERMANENT_ERROR_CODES else config.max_attempts
    status = mark_outbox_failed(outbox_id, error_code, max_attempts=max_attempts)
    return status


def _send_http_batch(config: PostHogConfig, items: list[dict[str, Any]], *, session=requests) -> tuple[bool, str | None]:
    try:
        resp = session.post(
            config.batch_url,
            json=build_posthog_body(config, items),
            timeout=config.timeout_seconds,
        )
    except requests.Timeout:
        return False, "timeout"
    except requests.ConnectionError:
        return False, "connection_error"
    except Exception:
        return False, "unexpected_error"
    if 200 <= int(resp.status_code) <= 299:
        return True, None
    return False, classify_http_status(int(resp.status_code))


def export_once(
    *,
    limit: int | None = None,
    dry_run: bool = False,
    event_uuid: str | None = None,
    worker_id: str | None = None,
    session=requests,
) -> ExportSummary:
    started = time.monotonic()
    config = load_posthog_config()
    batch_limit = max(1, min(int(limit or config.batch_size), config.batch_size))
    locked_by = worker_id or f"posthog-{socket.gethostname()}"

    if not dry_run and not config.can_send:
        return ExportSummary(duration_ms=int((time.monotonic() - started) * 1000), error_code=config.error_code)

    try:
        release_stale_claims(destination="posthog")
        suppress_unexportable_posthog_rows(max_event_age_days=config.max_event_age_days)
        claimed = claim_posthog_product_events(
            limit=batch_limit,
            locked_by=locked_by,
            max_event_age_days=config.max_event_age_days,
            event_uuid=event_uuid,
        )
    except Exception:
        return ExportSummary(duration_ms=int((time.monotonic() - started) * 1000), error_code="unexpected_error")

    valid: list[tuple[ClaimedProductEvent, dict[str, Any]]] = []
    skipped = 0
    dead_letter = 0
    retried = 0
    for ev in claimed:
        try:
            valid.append((ev, posthog_batch_item(ev)))
        except Exception:
            status = _mark_failure(ev.outbox_id, "malformed_event", config=config)
            dead_letter += 1 if status == "dead_letter" else 0
            retried += 1 if status == "retrying" else 0
            skipped += 1

    if dry_run:
        for ev, _item in valid:
            release_outbox_claim(ev.outbox_id, "dry_run_release")
        return ExportSummary(
            claimed=len(claimed),
            sent=0,
            retried=retried,
            dead_letter=dead_letter,
            skipped=skipped,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    if not valid:
        return ExportSummary(
            claimed=len(claimed),
            retried=retried,
            dead_letter=dead_letter,
            skipped=skipped,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    ok, error_code = _send_http_batch(config, [item for _ev, item in valid], session=session)
    if ok:
        for ev, _item in valid:
            mark_outbox_sent(ev.outbox_id)
        sent = len(valid)
    else:
        sent = 0
        for ev, _item in valid:
            status = _mark_failure(ev.outbox_id, error_code or "unexpected_error", config=config)
            dead_letter += 1 if status == "dead_letter" else 0
            retried += 1 if status == "retrying" else 0

    return ExportSummary(
        claimed=len(claimed),
        sent=sent,
        retried=retried,
        dead_letter=dead_letter,
        skipped=skipped,
        duration_ms=int((time.monotonic() - started) * 1000),
        error_code=error_code,
    )


def export_job_run(*, max_batches: int = 3, session=requests) -> ExportSummary:
    global _RUNNING
    if _RUNNING:
        return ExportSummary(error_code="overlap_prevented")
    config = load_posthog_config()
    if not config.can_send:
        return ExportSummary(error_code=config.error_code)
    _RUNNING = True
    started = time.monotonic()
    total = ExportSummary()
    try:
        claimed = sent = retried = dead_letter = skipped = 0
        last_error = None
        for _ in range(max(1, int(max_batches))):
            summary = export_once(session=session)
            claimed += summary.claimed
            sent += summary.sent
            retried += summary.retried
            dead_letter += summary.dead_letter
            skipped += summary.skipped
            last_error = summary.error_code or last_error
            if summary.claimed < config.batch_size:
                break
        total = ExportSummary(
            claimed=claimed,
            sent=sent,
            retried=retried,
            dead_letter=dead_letter,
            skipped=skipped,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_code=last_error,
        )
        log.info(
            "posthog_export: claimed=%s sent=%s retried=%s dead_letter=%s skipped=%s duration_ms=%s",
            total.claimed,
            total.sent,
            total.retried,
            total.dead_letter,
            total.skipped,
            total.duration_ms,
        )
        return total
    finally:
        _RUNNING = False


def export_status_counts() -> dict[str, Any]:
    config = load_posthog_config()
    rows = pg_fetchall(
        """
        SELECT
          COUNT(*) FILTER (WHERE status='pending') AS pending,
          COUNT(*) FILTER (WHERE status='retrying') AS retrying,
          COUNT(*) FILTER (WHERE status='claimed') AS processing,
          COUNT(*) FILTER (WHERE status='sent') AS sent,
          COUNT(*) FILTER (WHERE status='dead_letter') AS dead_letter,
          MIN(next_attempt_at) FILTER (WHERE status IN ('pending','retrying')) AS oldest_pending_timestamp,
          MAX(sent_at) FILTER (WHERE status='sent') AS last_sent_timestamp,
          MAX(last_error_code) FILTER (WHERE last_error_code IS NOT NULL) AS last_safe_error_code
        FROM analytics.event_outbox
        WHERE destination='posthog'
        """
    )
    r = rows[0]
    return {
        "enabled": config.can_send,
        "config_error": config.error_code or "none",
        "host": config.host or "not_configured",
        "pending": int(r[0] or 0),
        "retrying": int(r[1] or 0),
        "processing": int(r[2] or 0),
        "sent": int(r[3] or 0),
        "dead_letter": int(r[4] or 0),
        "oldest_pending_timestamp": r[5].isoformat() if r[5] else "none",
        "last_sent_timestamp": r[6].isoformat() if r[6] else "none",
        "last_safe_error_code": r[7] or "none",
    }


def dry_run_event_counts(*, limit: int | None = None, event_uuid: str | None = None) -> dict[str, int]:
    rows = preview_exportable_events(limit=limit, event_uuid=event_uuid)
    counts: dict[str, int] = {}
    for ev in rows:
        counts[ev.event_name] = counts.get(ev.event_name, 0) + 1
    return counts


def preview_exportable_events(*, limit: int | None = None, event_uuid: str | None = None) -> list[ClaimedProductEvent]:
    config = load_posthog_config()
    filters = [
        "o.destination='posthog'",
        "o.status IN ('pending','retrying')",
        "o.next_attempt_at <= now()",
        "p.deleted_at IS NULL",
        "p.external_export_allowed IS TRUE",
        "p.analytics_user_id IS NOT NULL",
        "p.occurred_at >= now() - (%s || ' days')::interval",
    ]
    params: list[Any] = [config.max_event_age_days]
    if event_uuid:
        filters.append("p.event_uuid=%s")
        params.append(str(event_uuid))
    params.append(max(1, int(limit or config.batch_size)))
    rows = pg_fetchall(
        f"""
        SELECT
            o.id, o.product_event_id, o.attempt_count,
            p.event_uuid, p.occurred_at, p.event_name, p.event_version,
            p.event_group, p.analytics_user_id, p.workspace_kind, p.source,
            p.platform, p.locale, p.currency, p.status, p.duration_ms,
            p.entity_type, p.entity_id, p.properties,
            p.external_export_allowed, p.deleted_at
          FROM analytics.event_outbox o
          JOIN analytics.product_events p ON p.id=o.product_event_id
         WHERE {' AND '.join(filters)}
         ORDER BY o.next_attempt_at, o.id
         LIMIT %s
        """,
        tuple(params),
    )
    return [
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
        for r in rows
    ]
