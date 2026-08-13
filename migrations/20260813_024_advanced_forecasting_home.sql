-- Advanced Forecasting & Home Intelligence.
-- Additive and idempotent. Apply only through the production deployment process.

BEGIN;

ALTER TABLE public.category_limits
    ADD COLUMN IF NOT EXISTS display_name TEXT,
    ADD COLUMN IF NOT EXISTS alerts_enabled BOOLEAN NOT NULL DEFAULT true;

CREATE TABLE IF NOT EXISTS public.forecast_snapshots (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    workspace_id BIGINT REFERENCES public.workspaces(id) ON DELETE CASCADE,
    workspace_scope_key BIGINT GENERATED ALWAYS AS (COALESCE(workspace_id, 0)) STORED,
    currency TEXT NOT NULL,
    period_key TEXT NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    as_of_date DATE NOT NULL,
    horizon_days INTEGER NOT NULL CHECK (horizon_days >= 0),
    feature_schema_version TEXT NOT NULL,
    features JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_fingerprint CHAR(64) NOT NULL,
    actual_variable_expense NUMERIC(18,2),
    actual_end_result NUMERIC(18,2),
    outcome_finalized_at TIMESTAMPTZ,
    invalidated_at TIMESTAMPTZ,
    invalidation_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT forecast_snapshots_period_valid CHECK (period_end >= period_start),
    CONSTRAINT forecast_snapshots_features_object CHECK (jsonb_typeof(features) = 'object')
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_forecast_snapshots_source
    ON public.forecast_snapshots(
        user_id, workspace_scope_key, currency, period_start, period_end,
        as_of_date, source_fingerprint
    );

CREATE INDEX IF NOT EXISTS idx_forecast_snapshots_outcome
    ON public.forecast_snapshots(period_end, outcome_finalized_at)
    WHERE invalidated_at IS NULL;

CREATE TABLE IF NOT EXISTS public.forecast_predictions (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL REFERENCES public.forecast_snapshots(id) ON DELETE CASCADE,
    model_family TEXT NOT NULL,
    model_version TEXT NOT NULL,
    risk_policy_version TEXT NOT NULL,
    q50 NUMERIC(18,2) NOT NULL,
    q80 NUMERIC(18,2) NOT NULL,
    q90 NUMERIC(18,2) NOT NULL,
    calibration_state TEXT NOT NULL,
    known_commitments NUMERIC(18,2) NOT NULL DEFAULT 0,
    goal_reserve NUMERIC(18,2) NOT NULL DEFAULT 0,
    general_budget_remaining NUMERIC(18,2),
    spendable_amount NUMERIC(18,2) NOT NULL,
    expected_end_result NUMERIC(18,2) NOT NULL,
    risk_state TEXT NOT NULL,
    quality_tier TEXT NOT NULL,
    reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    prediction_fingerprint CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT forecast_predictions_quantiles_valid CHECK (q50 <= q80 AND q80 <= q90),
    CONSTRAINT forecast_predictions_reasons_array CHECK (jsonb_typeof(reasons) = 'array')
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_forecast_predictions_fingerprint
    ON public.forecast_predictions(snapshot_id, prediction_fingerprint);

CREATE TABLE IF NOT EXISTS public.forecast_model_registry (
    id BIGSERIAL PRIMARY KEY,
    model_family TEXT NOT NULL,
    model_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('candidate', 'challenger', 'champion', 'retired', 'retrain_required')),
    artifact_path TEXT,
    artifact_sha256 CHAR(64),
    feature_schema_version TEXT NOT NULL,
    risk_policy_version TEXT NOT NULL,
    training_cutoff DATE,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    calibration JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT forecast_model_registry_metrics_object CHECK (jsonb_typeof(metrics) = 'object'),
    CONSTRAINT forecast_model_registry_calibration_object CHECK (jsonb_typeof(calibration) = 'object'),
    CONSTRAINT forecast_model_registry_version_unique UNIQUE (model_family, model_version)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_forecast_model_registry_champion
    ON public.forecast_model_registry(status)
    WHERE status='champion';

CREATE TABLE IF NOT EXISTS public.forecast_feedback (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    workspace_id BIGINT REFERENCES public.workspaces(id) ON DELETE CASCADE,
    workspace_scope_key BIGINT GENERATED ALWAYS AS (COALESCE(workspace_id, 0)) STORED,
    forecast_fingerprint CHAR(64) NOT NULL,
    feedback_type TEXT NOT NULL CHECK (feedback_type IN ('useful', 'not_useful')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT forecast_feedback_scope_unique UNIQUE (user_id, workspace_scope_key, forecast_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_forecast_feedback_user_updated
    ON public.forecast_feedback(user_id, updated_at DESC);

COMMIT;

-- Rollback (manual, only after application rollback):
-- DROP TABLE IF EXISTS public.forecast_feedback;
-- DROP TABLE IF EXISTS public.forecast_predictions;
-- DROP TABLE IF EXISTS public.forecast_snapshots;
-- DROP TABLE IF EXISTS public.forecast_model_registry;
-- ALTER TABLE public.category_limits DROP COLUMN IF EXISTS display_name;
-- ALTER TABLE public.category_limits DROP COLUMN IF EXISTS alerts_enabled;
