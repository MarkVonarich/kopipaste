-- Finuchet: personalized notifications, general limits, and combined category budgets.
-- Additive and idempotent. Do not apply from Codex; run through the deployment process.

BEGIN;

CREATE TABLE IF NOT EXISTS public.general_spending_limits (
    id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT REFERENCES public.workspaces(id),
    owner_user_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    amount BIGINT NOT NULL CHECK (amount > 0),
    currency TEXT NOT NULL DEFAULT 'RUB',
    period_type TEXT NOT NULL DEFAULT 'month',
    period_start DATE,
    period_end DATE,
    rolling_days INTEGER,
    mode TEXT NOT NULL DEFAULT 'expenses_only',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    alerts_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    notification_thresholds JSONB NOT NULL DEFAULT '[70,90,100,125,150,175,200]'::jsonb,
    exclusions JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT general_spending_limits_period_check CHECK (period_type IN ('week','month','calendar_month','custom','rolling_days')),
    CONSTRAINT general_spending_limits_mode_check CHECK (mode IN ('expenses_only'))
);

CREATE INDEX IF NOT EXISTS idx_general_limits_owner_workspace
    ON public.general_spending_limits(owner_user_id, workspace_id, enabled);

CREATE TABLE IF NOT EXISTS public.category_budget_groups (
    id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT REFERENCES public.workspaces(id),
    owner_user_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    amount BIGINT NOT NULL CHECK (amount > 0),
    currency TEXT NOT NULL DEFAULT 'RUB',
    period_type TEXT NOT NULL DEFAULT 'month',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    alerts_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    notification_thresholds JSONB NOT NULL DEFAULT '[70,90,100,125,150,175,200]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT category_budget_groups_period_check CHECK (period_type IN ('week','month','calendar_month','custom','rolling_days'))
);

CREATE INDEX IF NOT EXISTS idx_category_budget_groups_owner_workspace
    ON public.category_budget_groups(owner_user_id, workspace_id, enabled);

CREATE TABLE IF NOT EXISTS public.category_budget_group_members (
    group_id BIGINT NOT NULL REFERENCES public.category_budget_groups(id) ON DELETE CASCADE,
    category_name TEXT NOT NULL,
    normalized_category_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, normalized_category_name)
);

CREATE INDEX IF NOT EXISTS idx_category_budget_members_category
    ON public.category_budget_group_members(normalized_category_name);

CREATE TABLE IF NOT EXISTS public.limit_alert_deliveries (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    workspace_id BIGINT,
    entity_type TEXT NOT NULL,
    entity_id BIGINT,
    period_key TEXT NOT NULL,
    threshold INTEGER NOT NULL,
    dedupe_key TEXT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT limit_alert_deliveries_entity_check CHECK (entity_type IN ('category_limit','general_limit','category_budget_group'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_limit_alert_delivery_dedupe
    ON public.limit_alert_deliveries(user_id, entity_type, dedupe_key);

CREATE TABLE IF NOT EXISTS public.subscription_patterns (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    workspace_id BIGINT,
    normalized_merchant TEXT NOT NULL,
    category TEXT,
    amount BIGINT,
    currency TEXT NOT NULL DEFAULT 'RUB',
    confidence NUMERIC(5,4) NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'detected',
    last_operation_id BIGINT,
    last_seen_on DATE,
    next_expected_on DATE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT subscription_patterns_status_check CHECK (status IN ('detected','confirmed','suppressed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_subscription_pattern_scope
    ON public.subscription_patterns(user_id, COALESCE(workspace_id, 0), normalized_merchant, currency);

CREATE TABLE IF NOT EXISTS public.recurring_spend_patterns (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    workspace_id BIGINT,
    normalized_merchant TEXT NOT NULL,
    category TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'RUB',
    count INTEGER NOT NULL,
    total_amount BIGINT NOT NULL,
    average_amount NUMERIC(14,2) NOT NULL,
    monthly_estimate BIGINT NOT NULL,
    cadence TEXT NOT NULL,
    confidence NUMERIC(5,4) NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'detected',
    window_start DATE NOT NULL,
    window_end DATE NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT recurring_spend_patterns_status_check CHECK (status IN ('detected','hidden','subscription','dismissed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_recurring_spend_pattern_scope
    ON public.recurring_spend_patterns(user_id, COALESCE(workspace_id, 0), normalized_merchant, category, currency, window_start, window_end);

ALTER TABLE public.notification_preferences
    ADD COLUMN IF NOT EXISTS morning_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS evening_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS morning_time TIME,
    ADD COLUMN IF NOT EXISTS evening_time TIME,
    ADD COLUMN IF NOT EXISTS limit_alerts_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS budget_alerts_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS subscription_alerts_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS recurring_spend_alerts_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS weekly_reports_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS monthly_reports_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS quiet_hours_start TIME,
    ADD COLUMN IF NOT EXISTS quiet_hours_end TIME;

ALTER TABLE public.notification_events
    ADD COLUMN IF NOT EXISTS notification_priority INTEGER,
    ADD COLUMN IF NOT EXISTS related_entity_type TEXT,
    ADD COLUMN IF NOT EXISTS related_entity_id BIGINT,
    ADD COLUMN IF NOT EXISTS clicked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS clicked_action TEXT,
    ADD COLUMN IF NOT EXISTS converted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS conversion_type TEXT,
    ADD COLUMN IF NOT EXISTS context_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS locale TEXT,
    ADD COLUMN IF NOT EXISTS source_job TEXT,
    ADD COLUMN IF NOT EXISTS template_version TEXT;

ALTER TABLE public.operations ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

COMMIT;

-- Rollback notes:
-- DROP TABLE IF EXISTS public.recurring_spend_patterns;
-- DROP TABLE IF EXISTS public.subscription_patterns;
-- DROP TABLE IF EXISTS public.limit_alert_deliveries;
-- DROP TABLE IF EXISTS public.category_budget_group_members;
-- DROP TABLE IF EXISTS public.category_budget_groups;
-- DROP TABLE IF EXISTS public.general_spending_limits;
-- Added notification_preferences / notification_events / operations columns can remain safely unused by older code.
