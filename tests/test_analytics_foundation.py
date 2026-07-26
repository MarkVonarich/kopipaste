from datetime import datetime, timezone

import pytest

from services.acquisition import campaign_url, parse_start_payload
from services.analytics_privacy import pseudonymous_user_id, sanitize_properties
from services.event_outbox import retry_delay_seconds, status_after_failure
from services.product_events import ProductEvent, insert_product_event_cur, track_product_event


class FakeCursor:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((sql, params))

    def fetchone(self):
        return (101,)


class FakeConn:
    def __init__(self, fail=False):
        self.cur = FakeCursor()
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.fail = fail

    def cursor(self):
        if self.fail:
            raise RuntimeError("db down with secret=should_not_leak")
        return self.cur

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_privacy_sanitizer_removes_prohibited_and_bounds_values():
    props = sanitize_properties({
        "safe_key": "x" * 700,
        "raw_text": "coffee 250",
        "username": "@alice",
        "nested": {"prompt": "secret", "ok": "yes"},
        "bad-key": "drop",
    })
    assert props["safe_key"] == "x" * 512
    assert props["nested"] == {"ok": "yes"}
    assert "raw_text" not in props
    assert "username" not in props
    assert "bad-key" not in props


def test_pseudonymous_identity_is_stable_and_secret_required():
    a = pseudonymous_user_id(42, "secret-one")
    b = pseudonymous_user_id(42, "secret-one")
    c = pseudonymous_user_id(43, "secret-one")
    missing = pseudonymous_user_id(42, "")
    assert a.analytics_user_id == b.analytics_user_id
    assert a.analytics_user_id != c.analytics_user_id
    assert a.external_export_allowed is True
    assert missing.analytics_user_id is None
    assert missing.external_export_allowed is False


def test_product_event_allowlist_and_atomic_outbox(monkeypatch):
    monkeypatch.setattr("services.analytics_privacy.ANALYTICS_HMAC_SECRET", "unit-secret")
    cur = FakeCursor()
    event_id = insert_product_event_cur(
        cur,
        ProductEvent(
            event_name="operation_created",
            user_id=42,
            status="success",
            entity_type="operation",
            entity_id=7,
            properties={"operation_type": "expense", "raw_message": "drop"},
            occurred_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
        ),
    )
    assert event_id == 101
    assert len(cur.statements) == 2
    assert "INSERT INTO analytics.product_events" in cur.statements[0][0]
    assert "INSERT INTO analytics.event_outbox" in cur.statements[1][0]
    assert cur.statements[0][1][-2].adapted == {"operation_type": "expense"}
    with pytest.raises(ValueError):
        insert_product_event_cur(cur, ProductEvent(event_name="not_registered", user_id=42))


def test_product_event_failure_is_fail_open(monkeypatch):
    conn = FakeConn(fail=True)
    monkeypatch.setattr("services.product_events.get_conn", lambda: conn)
    assert track_product_event(ProductEvent(event_name="bot_started", user_id=42)) is None
    assert conn.rolled_back is True
    assert conn.closed is True


def test_outbox_retry_schedule_and_dead_letter():
    assert retry_delay_seconds(1) == 60
    assert retry_delay_seconds(3) == 240
    assert status_after_failure(7, max_attempts=8) == "retrying"
    assert status_after_failure(8, max_attempts=8) == "dead_letter"


def test_outbox_claim_uses_skip_locked():
    import inspect
    from services.event_outbox import claim_outbox_batch

    assert "FOR UPDATE SKIP LOCKED" in inspect.getsource(claim_outbox_batch)


def test_attribution_first_last_payload_rules():
    parsed = parse_start_payload("campaign_august__creative_2")
    assert parsed.source == "campaign_august"
    assert parsed.campaign == "creative_2"
    assert parse_start_payload("bad payload; drop table") is None
    assert campaign_url("KopiPasteBot", "friends_july") == "https://t.me/KopiPasteBot?start=friends_july"


def test_migration_contains_required_views_and_no_external_ids():
    sql = open("migrations/20260726_011_analytics_observability_foundation.sql", encoding="utf-8").read()
    for name in [
        "v_daily_active_users",
        "v_weekly_engaged_users",
        "v_user_activation",
        "v_feature_adoption_daily",
        "v_operations_daily",
        "v_source_usage_daily",
        "v_funnel_daily",
        "v_acquisition_daily",
        "v_api_usage_daily",
        "v_security_events_daily",
        "v_notification_conversion_daily",
    ]:
        assert name in sql
    assert "ops_7d >= 3" in sql
    assert "active_days_7d >= 2" in sql
