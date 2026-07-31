-- Finuchet: global automatic notification dispatcher and challenge foundation.
-- Additive and idempotent. Do not apply from Codex; run through the deployment process.

BEGIN;

ALTER TABLE public.notification_preferences
    ADD COLUMN IF NOT EXISTS challenge_notifications_enabled BOOLEAN NOT NULL DEFAULT TRUE;

CREATE TABLE IF NOT EXISTS public.automatic_notifications (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    workspace_id BIGINT,
    notification_type TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    template_key TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    delivery_policy TEXT NOT NULL,
    original_scheduled_at TIMESTAMPTZ NOT NULL,
    earliest_delivery_at TIMESTAMPTZ NOT NULL,
    timezone_name TEXT NOT NULL DEFAULT 'Europe/Moscow',
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    locked_at TIMESTAMPTZ,
    locked_by TEXT,
    sent_at TIMESTAMPTZ,
    skip_reason TEXT,
    last_error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT automatic_notifications_policy_check CHECK (delivery_policy IN ('defer','skip')),
    CONSTRAINT automatic_notifications_status_check CHECK (status IN ('pending','claimed','sent','skipped','dead_letter'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_automatic_notifications_dedupe
    ON public.automatic_notifications(user_id, notification_type, dedupe_key);

CREATE INDEX IF NOT EXISTS idx_automatic_notifications_due
    ON public.automatic_notifications(status, earliest_delivery_at, id)
    WHERE status IN ('pending','claimed');

CREATE TABLE IF NOT EXISTS public.user_challenge_assignments (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    workspace_id BIGINT,
    challenge_key TEXT NOT NULL,
    period_type TEXT NOT NULL,
    period_key TEXT NOT NULL,
    target INTEGER NOT NULL DEFAULT 1,
    progress INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    version INTEGER NOT NULL DEFAULT 1,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT user_challenge_assignments_period_check CHECK (period_type IN ('once','day','week','month')),
    CONSTRAINT user_challenge_assignments_status_check CHECK (status IN ('active','completed','expired','suppressed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_user_challenge_assignment_period
    ON public.user_challenge_assignments(user_id, COALESCE(workspace_id, 0), challenge_key, period_type, period_key);

CREATE INDEX IF NOT EXISTS idx_user_challenge_assignments_user_status
    ON public.user_challenge_assignments(user_id, status, period_type, period_key);

CREATE TABLE IF NOT EXISTS public.user_achievement_grants (
    user_id BIGINT NOT NULL,
    achievement_key TEXT NOT NULL,
    earned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (user_id, achievement_key)
);

COMMIT;

-- Rollback notes:
-- ALTER TABLE public.notification_preferences DROP COLUMN IF EXISTS challenge_notifications_enabled;
-- DROP TABLE IF EXISTS public.user_achievement_grants;
-- DROP TABLE IF EXISTS public.user_challenge_assignments;
-- DROP TABLE IF EXISTS public.automatic_notifications;
