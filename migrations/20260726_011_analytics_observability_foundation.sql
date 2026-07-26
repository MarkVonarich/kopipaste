-- Finuchet: analytics, observability, attribution, API usage and security-event foundation.
-- Additive and idempotent. Do not apply from Codex; run through the deployment process.

BEGIN;

CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS security;

CREATE TABLE IF NOT EXISTS analytics.product_events (
    id BIGSERIAL PRIMARY KEY,
    event_uuid UUID NOT NULL UNIQUE,
    occurred_at TIMESTAMPTZ NOT NULL,
    event_name TEXT NOT NULL,
    event_version SMALLINT NOT NULL DEFAULT 1 CHECK (event_version > 0),
    event_group TEXT NOT NULL,
    analytics_user_id TEXT,
    user_id BIGINT,
    workspace_id BIGINT,
    workspace_kind TEXT,
    session_id TEXT,
    source TEXT NOT NULL DEFAULT 'telegram',
    platform TEXT NOT NULL DEFAULT 'telegram',
    locale TEXT,
    currency TEXT,
    status TEXT,
    duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    entity_type TEXT,
    entity_id TEXT,
    properties JSONB NOT NULL DEFAULT '{}'::jsonb,
    external_export_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    anonymized_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT product_events_name_check CHECK (event_name ~ '^[a-z][a-z0-9_]{1,79}$'),
    CONSTRAINT product_events_properties_object CHECK (jsonb_typeof(properties) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_product_events_occurred_at ON analytics.product_events (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_product_events_name_time ON analytics.product_events (event_name, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_product_events_analytics_user_time ON analytics.product_events (analytics_user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_product_events_workspace_time ON analytics.product_events (workspace_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_product_events_retention ON analytics.product_events (created_at, deleted_at, anonymized_at);

CREATE TABLE IF NOT EXISTS analytics.event_outbox (
    id BIGSERIAL PRIMARY KEY,
    product_event_id BIGINT NOT NULL REFERENCES analytics.product_events(id) ON DELETE CASCADE,
    destination TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_at TIMESTAMPTZ,
    locked_by TEXT,
    sent_at TIMESTAMPTZ,
    last_error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT event_outbox_destination_check CHECK (destination IN ('posthog')),
    CONSTRAINT event_outbox_status_check CHECK (status IN ('pending','claimed','retrying','sent','dead_letter','suppressed'))
);

CREATE INDEX IF NOT EXISTS idx_event_outbox_claim
    ON analytics.event_outbox (destination, status, next_attempt_at, id)
    WHERE status IN ('pending','retrying');
CREATE INDEX IF NOT EXISTS idx_event_outbox_product_event ON analytics.event_outbox (product_event_id);
CREATE INDEX IF NOT EXISTS idx_event_outbox_retention ON analytics.event_outbox (created_at, sent_at);

CREATE TABLE IF NOT EXISTS analytics.acquisition_attribution (
    user_id BIGINT PRIMARY KEY,
    analytics_user_id TEXT,
    first_touch_source TEXT NOT NULL,
    first_touch_campaign TEXT,
    first_touch_content TEXT,
    first_touch_referral_code TEXT,
    first_touch_at TIMESTAMPTZ NOT NULL,
    last_touch_source TEXT NOT NULL,
    last_touch_campaign TEXT,
    last_touch_content TEXT,
    last_touch_referral_code TEXT,
    last_touch_at TIMESTAMPTZ NOT NULL,
    invited_by_analytics_user_id TEXT,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT acquisition_token_lengths CHECK (
        length(first_touch_source) <= 64
        AND (first_touch_campaign IS NULL OR length(first_touch_campaign) <= 64)
        AND (first_touch_content IS NULL OR length(first_touch_content) <= 64)
        AND (first_touch_referral_code IS NULL OR length(first_touch_referral_code) <= 64)
        AND length(last_touch_source) <= 64
        AND (last_touch_campaign IS NULL OR length(last_touch_campaign) <= 64)
        AND (last_touch_content IS NULL OR length(last_touch_content) <= 64)
        AND (last_touch_referral_code IS NULL OR length(last_touch_referral_code) <= 64)
    )
);

CREATE INDEX IF NOT EXISTS idx_acquisition_first_touch ON analytics.acquisition_attribution (first_touch_at DESC, first_touch_source);
CREATE INDEX IF NOT EXISTS idx_acquisition_last_touch ON analytics.acquisition_attribution (last_touch_at DESC, last_touch_source);
CREATE INDEX IF NOT EXISTS idx_acquisition_analytics_user ON analytics.acquisition_attribution (analytics_user_id);

CREATE TABLE IF NOT EXISTS analytics.api_usage_events (
    id BIGSERIAL PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    feature TEXT NOT NULL,
    status TEXT NOT NULL,
    analytics_user_id TEXT,
    user_id BIGINT,
    workspace_id BIGINT,
    request_count INTEGER NOT NULL DEFAULT 1 CHECK (request_count > 0),
    latency_ms INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
    input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
    estimated_cost_usd NUMERIC(12,6),
    error_code TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT api_usage_metadata_object CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_api_usage_time ON analytics.api_usage_events (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_usage_feature_time ON analytics.api_usage_events (feature, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_usage_provider_model_time ON analytics.api_usage_events (provider, model, occurred_at DESC);

CREATE TABLE IF NOT EXISTS security.security_events (
    id BIGSERIAL PRIMARY KEY,
    event_uuid UUID NOT NULL UNIQUE,
    occurred_at TIMESTAMPTZ NOT NULL,
    event_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    analytics_user_id TEXT,
    user_id BIGINT,
    workspace_id BIGINT,
    chat_type TEXT,
    source TEXT NOT NULL DEFAULT 'telegram',
    rule_key TEXT,
    risk_score SMALLINT CHECK (risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)),
    action_taken TEXT NOT NULL DEFAULT 'monitor_only',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT security_events_name_check CHECK (event_name ~ '^[a-z][a-z0-9_]{1,79}$'),
    CONSTRAINT security_events_severity_check CHECK (severity IN ('info','warning','high','critical')),
    CONSTRAINT security_events_metadata_object CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_security_events_time ON security.security_events (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_events_name_time ON security.security_events (event_name, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_events_severity_time ON security.security_events (severity, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_events_user_time ON security.security_events (analytics_user_id, occurred_at DESC);

CREATE OR REPLACE VIEW analytics.v_daily_active_users AS
SELECT date_trunc('day', occurred_at)::date AS event_date,
       COUNT(DISTINCT analytics_user_id) AS active_users
  FROM analytics.product_events
 WHERE analytics_user_id IS NOT NULL
   AND deleted_at IS NULL
 GROUP BY 1;

CREATE OR REPLACE VIEW analytics.v_weekly_engaged_users AS
WITH user_days AS (
    SELECT analytics_user_id,
           date_trunc('day', occurred_at)::date AS event_date,
           COUNT(*) FILTER (WHERE event_name='operation_created' AND status='success') AS successful_operations
      FROM analytics.product_events
     WHERE analytics_user_id IS NOT NULL
       AND deleted_at IS NULL
     GROUP BY 1, 2
),
rolling AS (
    SELECT d.event_date,
           u.analytics_user_id,
           SUM(u.successful_operations) AS ops_7d,
           COUNT(*) FILTER (WHERE u.successful_operations > 0) AS active_days_7d
      FROM (SELECT DISTINCT event_date FROM user_days) d
      JOIN user_days u ON u.event_date BETWEEN d.event_date - INTERVAL '6 days' AND d.event_date
     GROUP BY d.event_date, u.analytics_user_id
)
SELECT event_date, COUNT(*) AS weekly_engaged_users
  FROM rolling
 WHERE ops_7d >= 3 AND active_days_7d >= 2
 GROUP BY 1;

CREATE OR REPLACE VIEW analytics.v_user_activation AS
SELECT analytics_user_id,
       MIN(occurred_at) FILTER (WHERE event_name='bot_started') AS first_started_at,
       MIN(occurred_at) FILTER (WHERE event_name='onboarding_completed') AS onboarding_completed_at,
       MIN(occurred_at) FILTER (WHERE event_name='operation_created' AND status='success') AS first_operation_at
  FROM analytics.product_events
 WHERE analytics_user_id IS NOT NULL
   AND deleted_at IS NULL
 GROUP BY analytics_user_id;

CREATE OR REPLACE VIEW analytics.v_feature_adoption_daily AS
SELECT date_trunc('day', occurred_at)::date AS event_date,
       event_group,
       event_name,
       COUNT(*) AS events,
       COUNT(DISTINCT analytics_user_id) AS users
  FROM analytics.product_events
 WHERE deleted_at IS NULL
 GROUP BY 1, 2, 3;

CREATE OR REPLACE VIEW analytics.v_operations_daily AS
SELECT date_trunc('day', occurred_at)::date AS event_date,
       source,
       status,
       COUNT(*) AS events,
       COUNT(DISTINCT analytics_user_id) AS users
  FROM analytics.product_events
 WHERE event_name IN ('operation_created','operation_failed','operation_edited','operation_deleted')
   AND deleted_at IS NULL
 GROUP BY 1, 2, 3;

CREATE OR REPLACE VIEW analytics.v_source_usage_daily AS
SELECT date_trunc('day', occurred_at)::date AS event_date,
       source,
       COUNT(*) AS events,
       COUNT(DISTINCT analytics_user_id) AS users
  FROM analytics.product_events
 WHERE deleted_at IS NULL
 GROUP BY 1, 2;

CREATE OR REPLACE VIEW analytics.v_funnel_daily AS
SELECT date_trunc('day', occurred_at)::date AS event_date,
       event_name,
       status,
       COUNT(*) AS events,
       COUNT(DISTINCT analytics_user_id) AS users
  FROM analytics.product_events
 WHERE event_name IN ('bot_started','onboarding_started','onboarding_completed','operation_started','operation_created','export_started','export_completed','export_failed')
   AND deleted_at IS NULL
 GROUP BY 1, 2, 3;

CREATE OR REPLACE VIEW analytics.v_acquisition_daily AS
SELECT date_trunc('day', first_touch_at)::date AS event_date,
       first_touch_source,
       first_touch_campaign,
       COUNT(*) AS attributed_users
  FROM analytics.acquisition_attribution
 WHERE deleted_at IS NULL
 GROUP BY 1, 2, 3;

CREATE OR REPLACE VIEW analytics.v_api_usage_daily AS
SELECT date_trunc('day', occurred_at)::date AS event_date,
       provider,
       model,
       feature,
       status,
       SUM(request_count) AS requests,
       SUM(input_tokens) AS input_tokens,
       SUM(output_tokens) AS output_tokens,
       SUM(estimated_cost_usd) AS estimated_cost_usd,
       COUNT(DISTINCT analytics_user_id) AS users
  FROM analytics.api_usage_events
 GROUP BY 1, 2, 3, 4, 5;

CREATE OR REPLACE VIEW analytics.v_security_events_daily AS
SELECT date_trunc('day', occurred_at)::date AS event_date,
       event_name,
       severity,
       COUNT(*) AS events,
       COUNT(DISTINCT analytics_user_id) AS users
  FROM security.security_events
 GROUP BY 1, 2, 3;

CREATE OR REPLACE VIEW analytics.v_notification_conversion_daily AS
SELECT date_trunc('day', occurred_at)::date AS event_date,
       COUNT(*) FILTER (WHERE event_name='notification_sent') AS sent,
       COUNT(*) FILTER (WHERE event_name='notification_clicked') AS clicked,
       COUNT(*) FILTER (WHERE event_name='notification_converted') AS converted,
       COUNT(DISTINCT analytics_user_id) FILTER (WHERE event_name='notification_sent') AS notified_users
  FROM analytics.product_events
 WHERE event_name IN ('notification_sent','notification_clicked','notification_converted')
   AND deleted_at IS NULL
 GROUP BY 1;

COMMIT;

-- Estimated storage: product_events with bounded JSONB is roughly 0.8-1.5 GB per 1M rows
-- including indexes; api_usage/security/outbox are usually smaller and depend on retention.
-- Rollback notes: DROP VIEW analytics.v_*; DROP TABLE analytics.event_outbox,
-- analytics.product_events, analytics.acquisition_attribution, analytics.api_usage_events,
-- security.security_events; DROP SCHEMA analytics; DROP SCHEMA security.
