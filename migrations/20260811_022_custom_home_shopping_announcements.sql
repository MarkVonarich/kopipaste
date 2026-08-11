-- Product Evolution v3 PR2: custom Home, shared shopping, and announcement state.
-- Additive and idempotent. Apply only through the production deployment process.

BEGIN;

CREATE TABLE IF NOT EXISTS public.user_home_preferences (
    user_id BIGINT PRIMARY KEY REFERENCES public.users(user_id) ON DELETE CASCADE,
    widget_order JSONB NOT NULL DEFAULT '[]'::jsonb,
    enabled_widgets JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT user_home_preferences_order_array CHECK (jsonb_typeof(widget_order) = 'array'),
    CONSTRAINT user_home_preferences_enabled_array CHECK (jsonb_typeof(enabled_widgets) = 'array')
);

CREATE TABLE IF NOT EXISTS public.shopping_items (
    id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL REFERENCES public.workspaces(id) ON DELETE CASCADE,
    item_text VARCHAR(200) NOT NULL,
    created_by BIGINT,
    updated_by BIGINT,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT shopping_items_text_not_blank CHECK (length(btrim(item_text)) > 0)
);

CREATE INDEX IF NOT EXISTS idx_shopping_items_workspace_active
    ON public.shopping_items(workspace_id, completed_at NULLS FIRST, created_at DESC);

CREATE TABLE IF NOT EXISTS public.user_announcement_state (
    user_id BIGINT NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    candidate_id TEXT NOT NULL,
    dismissed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, candidate_id)
);

COMMIT;

-- Rollback:
-- DROP TABLE IF EXISTS public.user_announcement_state;
-- DROP TABLE IF EXISTS public.shopping_items;
-- DROP TABLE IF EXISTS public.user_home_preferences;
