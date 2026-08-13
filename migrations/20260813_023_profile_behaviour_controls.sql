-- Product Evolution v3 PR5: profile behaviour and personal category preferences.
-- Additive and idempotent. Apply only through the production deployment process.

BEGIN;

ALTER TABLE public.notification_preferences
    ADD COLUMN IF NOT EXISTS vacation_enabled BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS vacation_start DATE,
    ADD COLUMN IF NOT EXISTS vacation_end DATE;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'public.notification_preferences'::regclass
           AND conname = 'notification_preferences_vacation_dates_valid'
    ) THEN
        ALTER TABLE public.notification_preferences
            ADD CONSTRAINT notification_preferences_vacation_dates_valid
            CHECK (vacation_start IS NULL OR vacation_end IS NULL OR vacation_end >= vacation_start);
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS public.user_category_preferences (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    workspace_id BIGINT REFERENCES public.workspaces(id) ON DELETE CASCADE,
    workspace_scope_key BIGINT GENERATED ALWAYS AS (COALESCE(workspace_id, 0)) STORED,
    category_key TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal',
    relevant BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT user_category_preferences_category_key_not_blank CHECK (length(btrim(category_key)) > 0),
    CONSTRAINT user_category_preferences_operation_type_valid CHECK (operation_type IN ('Расходы', 'Доходы')),
    CONSTRAINT user_category_preferences_priority_valid CHECK (priority IN ('normal', 'high')),
    CONSTRAINT user_category_preferences_scope_unique UNIQUE (user_id, workspace_scope_key, operation_type, category_key)
);

CREATE INDEX IF NOT EXISTS idx_user_category_preferences_scope
    ON public.user_category_preferences(user_id, workspace_scope_key, operation_type);

COMMIT;

-- Rollback:
-- DROP TABLE IF EXISTS public.user_category_preferences;
-- ALTER TABLE public.notification_preferences DROP CONSTRAINT IF EXISTS notification_preferences_vacation_dates_valid;
-- ALTER TABLE public.notification_preferences DROP COLUMN IF EXISTS vacation_end;
-- ALTER TABLE public.notification_preferences DROP COLUMN IF EXISTS vacation_start;
-- ALTER TABLE public.notification_preferences DROP COLUMN IF EXISTS vacation_enabled;
