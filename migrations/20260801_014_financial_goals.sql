-- Finuchet: first production financial goals foundation.
-- Additive and idempotent. Do not apply from Codex; run through the deployment process.
-- Production command:
--   psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f migrations/20260801_014_financial_goals.sql

BEGIN;

ALTER TABLE public.notification_preferences
    ADD COLUMN IF NOT EXISTS goal_notifications_enabled BOOLEAN DEFAULT FALSE;

ALTER TABLE public.notification_preferences
    ALTER COLUMN goal_notifications_enabled SET DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS public.financial_goals (
    id BIGSERIAL PRIMARY KEY,
    owner_user_id BIGINT NOT NULL,
    workspace_id BIGINT,
    display_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'RUB',
    target_amount NUMERIC(14,2) NOT NULL,
    current_balance NUMERIC(14,2) NOT NULL DEFAULT 0,
    deadline DATE,
    strategy TEXT NOT NULL DEFAULT 'none',
    frequency TEXT NOT NULL DEFAULT 'none',
    comfortable_amount NUMERIC(14,2),
    planned_contribution_amount NUMERIC(14,2),
    schedule_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    projected_completion_date DATE,
    next_contribution_date DATE,
    status TEXT NOT NULL DEFAULT 'active',
    reminders_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    salary_categories TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    plan_start_date DATE NOT NULL DEFAULT CURRENT_DATE,
    achieved_at TIMESTAMPTZ,
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    version INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT financial_goals_target_positive CHECK (target_amount > 0),
    CONSTRAINT financial_goals_balance_nonnegative CHECK (current_balance >= 0),
    CONSTRAINT financial_goals_strategy_check CHECK (strategy IN ('none','deadline','contribution')),
    CONSTRAINT financial_goals_frequency_check CHECK (frequency IN ('none','monthly','twice_monthly','weekly','salary_monthly','salary_twice_monthly')),
    CONSTRAINT financial_goals_status_check CHECK (status IN ('active','achieved','paused','archived','deleted')),
    CONSTRAINT financial_goals_schedule_object CHECK (jsonb_typeof(schedule_config) = 'object')
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_financial_goals_active_name
    ON public.financial_goals(owner_user_id, COALESCE(workspace_id, 0), normalized_name)
    WHERE status IN ('active','achieved','paused');

CREATE INDEX IF NOT EXISTS idx_financial_goals_owner_status
    ON public.financial_goals(owner_user_id, COALESCE(workspace_id, 0), status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_financial_goals_next_reminder
    ON public.financial_goals(status, reminders_enabled, next_contribution_date)
    WHERE status='active' AND reminders_enabled;

CREATE TABLE IF NOT EXISTS public.goal_movements (
    id BIGSERIAL PRIMARY KEY,
    goal_id BIGINT NOT NULL REFERENCES public.financial_goals(id) ON DELETE CASCADE,
    actor_user_id BIGINT NOT NULL,
    movement_type TEXT NOT NULL,
    amount NUMERIC(14,2) NOT NULL,
    balance_after NUMERIC(14,2) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source TEXT NOT NULL DEFAULT 'manual',
    linked_operation_id BIGINT REFERENCES public.operations(id) ON DELETE SET NULL,
    idempotency_key TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT goal_movements_type_check CHECK (movement_type IN ('initial','contribution','withdrawal','adjustment')),
    CONSTRAINT goal_movements_amount_positive CHECK (amount > 0),
    CONSTRAINT goal_movements_balance_nonnegative CHECK (balance_after >= 0),
    CONSTRAINT goal_movements_metadata_object CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_goal_movements_idempotency
    ON public.goal_movements(idempotency_key);

CREATE INDEX IF NOT EXISTS idx_goal_movements_goal_time
    ON public.goal_movements(goal_id, occurred_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_goal_movements_linked_operation
    ON public.goal_movements(linked_operation_id)
    WHERE linked_operation_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.goal_drafts (
    draft_id UUID PRIMARY KEY DEFAULT (md5(random()::text || clock_timestamp()::text))::uuid,
    owner_user_id BIGINT NOT NULL,
    workspace_id BIGINT,
    current_step TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'draft',
    expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '30 minutes',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT goal_drafts_status_check CHECK (status IN ('draft','committed','cancelled','expired')),
    CONSTRAINT goal_drafts_payload_object CHECK (jsonb_typeof(payload) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_goal_drafts_owner_status
    ON public.goal_drafts(owner_user_id, COALESCE(workspace_id, 0), status, expires_at);

COMMIT;

-- Rollback notes:
--   ALTER TABLE public.notification_preferences DROP COLUMN IF EXISTS goal_notifications_enabled;
--   DROP TABLE IF EXISTS public.goal_drafts;
--   DROP TABLE IF EXISTS public.goal_movements;
--   DROP TABLE IF EXISTS public.financial_goals;
--
-- Do not use the destructive table drops after real users create goals unless
-- product has approved permanent loss of goal plans and movement ledgers.
