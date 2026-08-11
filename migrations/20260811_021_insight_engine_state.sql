-- Insight Engine v1: bounded lifecycle, repeat suppression, and feedback state.
-- Additive and idempotent. Apply only through the production deployment process.

BEGIN;

CREATE TABLE IF NOT EXISTS public.insight_states (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    workspace_id BIGINT REFERENCES public.workspaces(id) ON DELETE CASCADE,
    workspace_scope_key BIGINT GENERATED ALWAYS AS (COALESCE(workspace_id, 0)) STORED,
    fingerprint CHAR(64) NOT NULL,
    detector_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    currency TEXT NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    comparison_start DATE NOT NULL,
    comparison_end DATE NOT NULL,
    first_shown_at TIMESTAMPTZ,
    last_shown_at TIMESTAMPTZ,
    show_count INTEGER NOT NULL DEFAULT 0 CHECK (show_count >= 0),
    feedback_type TEXT,
    feedback_at TIMESTAMPTZ,
    suppression_until TIMESTAMPTZ,
    valid_until TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT insight_states_feedback_check
        CHECK (feedback_type IS NULL OR feedback_type IN ('useful', 'not_useful')),
    CONSTRAINT insight_states_period_check
        CHECK (period_end >= period_start AND comparison_end >= comparison_start)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_insight_states_scope_fingerprint
    ON public.insight_states(user_id, workspace_scope_key, fingerprint);

CREATE INDEX IF NOT EXISTS idx_insight_states_feedback_suppression
    ON public.insight_states(user_id, workspace_scope_key, detector_type, suppression_until DESC)
    WHERE feedback_type='not_useful';

CREATE INDEX IF NOT EXISTS idx_insight_states_lifecycle
    ON public.insight_states(valid_until, updated_at);

COMMIT;

-- Rollback: DROP TABLE IF EXISTS public.insight_states;
