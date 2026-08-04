-- Finuchet Telegram Mini App foundation.
--
-- Production command:
--   psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f migrations/20260804_016_miniapp_foundation.sql
--
-- Rollback:
--   DROP TABLE IF EXISTS public.miniapp_idempotency_keys;
--   DROP TABLE IF EXISTS public.miniapp_user_preferences;

CREATE TABLE IF NOT EXISTS public.miniapp_user_preferences (
    user_id BIGINT PRIMARY KEY REFERENCES public.users(user_id) ON DELETE CASCADE,
    theme TEXT NOT NULL DEFAULT 'telegram',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT miniapp_user_preferences_theme_check
        CHECK (theme IN ('telegram', 'light', 'dark'))
);

CREATE TABLE IF NOT EXISTS public.miniapp_idempotency_keys (
    user_id BIGINT NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    operation_id BIGINT REFERENCES public.operations(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    response_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, idempotency_key),
    CONSTRAINT miniapp_idempotency_status_check
        CHECK (status IN ('pending', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_miniapp_idempotency_operation
    ON public.miniapp_idempotency_keys(operation_id)
    WHERE operation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_miniapp_idempotency_created
    ON public.miniapp_idempotency_keys(created_at);

CREATE INDEX IF NOT EXISTS idx_operations_miniapp_workspace_filters
    ON public.operations(workspace_id, op_date DESC, id DESC)
    WHERE workspace_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_operations_miniapp_legacy_personal_filters
    ON public.operations(user_id, op_date DESC, id DESC)
    WHERE workspace_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_operations_miniapp_search
    ON public.operations(user_id, op_date DESC, category, id DESC);
