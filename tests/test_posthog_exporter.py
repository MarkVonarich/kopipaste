from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import requests

from services.event_outbox import ClaimedProductEvent
from services.posthog_exporter import (
    ExportSummary,
    build_posthog_body,
    classify_http_status,
    dry_run_event_counts,
    export_job_run,
    export_once,
    load_posthog_config,
    posthog_batch_item,
)


def _event(**overrides):
    base = dict(
        outbox_id=1,
        product_event_id=10,
        attempt_count=0,
        event_uuid="11111111-1111-4111-8111-111111111111",
        occurred_at=datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc),
        event_name="operation_created",
        event_version=1,
        event_group="operations",
        analytics_user_id="au_safe",
        workspace_kind="personal",
        source="telegram",
        platform="telegram",
        locale="ru",
        currency="RUB",
        status="success",
        duration_ms=12,
        entity_type="operation",
        entity_id="op_safe",
    properties={"category": "Coffee", "raw_text": "drop", "chat_id": 123, "amount": 250, "test": True},
        external_export_allowed=True,
        deleted_at=None,
    )
    base.update(overrides)
    return ClaimedProductEvent(**base)


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code
        self.text = "must not be stored"


class _Session:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append((url, json, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_posthog_config_validation(monkeypatch):
    monkeypatch.setattr("services.posthog_exporter.POSTHOG_EXPORT_ENABLED", False)
    assert load_posthog_config().error_code == "disabled"
    monkeypatch.setattr("services.posthog_exporter.POSTHOG_EXPORT_ENABLED", True)
    monkeypatch.setattr("services.posthog_exporter.POSTHOG_PROJECT_TOKEN", "")
    monkeypatch.setattr("services.posthog_exporter.POSTHOG_HOST", "https://eu.i.posthog.com")
    assert load_posthog_config().error_code == "config_missing"
    monkeypatch.setattr("services.posthog_exporter.POSTHOG_PROJECT_TOKEN", "phc_secret")
    monkeypatch.setattr("services.posthog_exporter.POSTHOG_HOST", "http://eu.i.posthog.com")
    assert load_posthog_config().error_code == "invalid_host"
    monkeypatch.setattr("services.posthog_exporter.POSTHOG_HOST", "https://eu.i.posthog.com/path")
    assert load_posthog_config().error_code == "invalid_host"
    monkeypatch.setattr("services.posthog_exporter.POSTHOG_HOST", "https://eu.i.posthog.com")
    cfg = load_posthog_config()
    assert cfg.error_code is None
    assert cfg.batch_url == "https://eu.i.posthog.com/batch/"


def test_posthog_mapping_privacy_and_body():
    item = posthog_batch_item(_event())
    assert item["event"] == "operation_created"
    assert item["timestamp"] == "2026-07-26T12:00:00+00:00"
    props = item["properties"]
    assert props["distinct_id"] == "au_safe"
    assert props["event_uuid"] == "11111111-1111-4111-8111-111111111111"
    assert props["event_version"] == 1
    assert props["event_group"] == "operations"
    assert props["source"] == "telegram"
    assert props["platform"] == "telegram"
    assert props["workspace_kind"] == "personal"
    assert props["locale"] == "ru"
    assert props["currency"] == "RUB"
    assert props["duration_ms"] == 12
    assert props["category"] == "Coffee"
    assert "user_id" not in props
    assert "chat_id" not in props
    assert "amount" not in props
    assert "raw_text" not in props
    body = build_posthog_body(SimpleNamespace(project_token="phc_secret"), [item])
    assert body["api_key"] == "phc_secret"
    assert body["historical_migration"] is False
    assert body["batch"] == [item]


def test_malformed_event_rejected_locally():
    for ev in [
        _event(analytics_user_id=None),
        _event(external_export_allowed=False),
        _event(deleted_at=datetime.now(timezone.utc)),
        _event(properties={"safe_key": "x" * (40 * 1024)}),
    ]:
        try:
            posthog_batch_item(ev)
        except ValueError:
            pass
        else:
            raise AssertionError("expected local rejection")


def _patch_export(monkeypatch, events, response):
    monkeypatch.setattr("services.posthog_exporter.POSTHOG_EXPORT_ENABLED", True)
    monkeypatch.setattr("services.posthog_exporter.POSTHOG_PROJECT_TOKEN", "phc_secret")
    monkeypatch.setattr("services.posthog_exporter.POSTHOG_HOST", "https://eu.i.posthog.com")
    monkeypatch.setattr("services.posthog_exporter.release_stale_claims", lambda **_kwargs: 0)
    monkeypatch.setattr("services.posthog_exporter.suppress_unexportable_posthog_rows", lambda **_kwargs: 0)
    monkeypatch.setattr("services.posthog_exporter.claim_posthog_product_events", lambda **_kwargs: list(events))
    sent = []
    failed = []
    monkeypatch.setattr("services.posthog_exporter.mark_outbox_sent", lambda oid: sent.append(oid))

    def _failed(oid, err, max_attempts):
        failed.append((oid, err, max_attempts))
        return "dead_letter" if max_attempts == 1 else "retrying"

    monkeypatch.setattr("services.posthog_exporter.mark_outbox_failed", _failed)
    return sent, failed, _Session(response)


def test_export_success_marks_sent(monkeypatch):
    sent, failed, session = _patch_export(monkeypatch, [_event()], _Response(200))
    summary = export_once(session=session)
    assert summary.sent == 1
    assert sent == [1]
    assert failed == []
    assert session.calls[0][0] == "https://eu.i.posthog.com/batch/"
    assert "phc_secret" not in str(summary)


def test_export_retryable_failures(monkeypatch):
    for response, code in [
        (_Response(429), "http_429"),
        (_Response(500), "http_5xx"),
        (requests.Timeout(), "timeout"),
        (requests.ConnectionError(), "connection_error"),
    ]:
        sent, failed, session = _patch_export(monkeypatch, [_event()], response)
        summary = export_once(session=session)
        assert summary.error_code == code
        assert summary.retried == 1
        assert sent == []
        assert failed[0][1] == code
        assert failed[0][2] == 8


def test_export_permanent_http_400_dead_letters(monkeypatch):
    _sent, failed, session = _patch_export(monkeypatch, [_event()], _Response(400))
    summary = export_once(session=session)
    assert summary.error_code == "http_400"
    assert summary.dead_letter == 1
    assert failed[0] == (1, "http_400", 1)


def test_one_malformed_event_does_not_block_valid(monkeypatch):
    sent, failed, session = _patch_export(monkeypatch, [_event(outbox_id=1), _event(outbox_id=2, analytics_user_id=None)], _Response(200))
    summary = export_once(session=session)
    assert summary.claimed == 2
    assert summary.sent == 1
    assert summary.skipped == 1
    assert sent == [1]
    assert failed[0] == (2, "malformed_event", 1)


def test_disabled_export_does_not_call_network(monkeypatch):
    monkeypatch.setattr("services.posthog_exporter.POSTHOG_EXPORT_ENABLED", False)
    session = _Session(_Response(200))
    summary = export_once(session=session)
    assert summary.error_code == "disabled"
    assert session.calls == []


def test_classify_http_status():
    assert classify_http_status(401) == "http_401"
    assert classify_http_status(403) == "http_403"
    assert classify_http_status(413) == "http_413"
    assert classify_http_status(503) == "http_5xx"


def test_dry_run_counts_are_read_only(monkeypatch):
    monkeypatch.setattr("services.posthog_exporter.preview_exportable_events", lambda **_kwargs: [_event(), _event(event_name="bot_started")])
    assert dry_run_event_counts(limit=2) == {"operation_created": 1, "bot_started": 1}


def test_scheduler_overlap_prevented(monkeypatch):
    monkeypatch.setattr("services.posthog_exporter._RUNNING", True)
    assert export_job_run().error_code == "overlap_prevented"


def test_scheduler_enabled_exports(monkeypatch):
    monkeypatch.setattr("services.posthog_exporter.POSTHOG_EXPORT_ENABLED", True)
    monkeypatch.setattr("services.posthog_exporter.POSTHOG_PROJECT_TOKEN", "phc_secret")
    monkeypatch.setattr("services.posthog_exporter.POSTHOG_HOST", "https://eu.i.posthog.com")
    calls = []
    monkeypatch.setattr("services.posthog_exporter.export_once", lambda session=None: calls.append(1) or ExportSummary(claimed=0))
    summary = export_job_run(session=_Session(_Response(200)))
    assert summary.claimed == 0
    assert calls == [1]


def test_admin_posthog_commands(monkeypatch):
    from routers import commands

    replies = []
    async def _reply(text):
        replies.append(text)

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(reply_text=_reply),
    )
    context = SimpleNamespace()
    monkeypatch.setattr(commands, "ADMIN_USER_IDS", [42])
    monkeypatch.setattr(commands, "export_status_counts", lambda: {
        "enabled": False,
        "pending": 1,
        "retrying": 2,
        "sent": 3,
        "dead_letter": 4,
        "last_sent_timestamp": "none",
        "last_safe_error_code": "none",
    })
    asyncio.run(commands.cmd_admin_posthog_status(update, context))
    assert "pending: 1" in replies[-1]

    queued = []
    monkeypatch.setattr(commands, "track_product_event", lambda ev: queued.append(ev) or 99)
    asyncio.run(commands.cmd_admin_posthog_test_event(update, context))
    assert queued[-1].event_name == "posthog_connection_test"
    assert queued[-1].source == "admin_test"
    assert queued[-1].properties == {"test": True}


def test_non_admin_posthog_command_denied(monkeypatch):
    from routers import commands

    replies = []
    async def _reply(text):
        replies.append(text)

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7),
        message=SimpleNamespace(reply_text=_reply),
    )
    monkeypatch.setattr(commands, "ADMIN_USER_IDS", [42])
    monkeypatch.setattr(commands, "track_security_event", lambda *_args, **_kwargs: None)
    asyncio.run(commands.cmd_admin_posthog_status(update, SimpleNamespace()))
    assert "только для администратора" in replies[-1]
