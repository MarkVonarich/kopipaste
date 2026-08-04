-- Finuchet Mini App PR1 acceptance hardening.
--
-- Production command:
--   psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f migrations/20260804_017_miniapp_acceptance_hardening.sql
--
-- Rollback:
--   DROP TABLE IF EXISTS public.miniapp_rate_limits;

CREATE TABLE IF NOT EXISTS public.miniapp_rate_limits (
    user_id BIGINT NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    bucket BIGINT NOT NULL,
    write_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, bucket)
);

CREATE INDEX IF NOT EXISTS idx_miniapp_rate_limits_updated
    ON public.miniapp_rate_limits(updated_at);
