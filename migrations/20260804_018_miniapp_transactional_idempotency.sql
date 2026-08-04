-- Finuchet Mini App transactional idempotency hardening.
--
-- Production command:
--   psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f migrations/20260804_018_miniapp_transactional_idempotency.sql
--
-- Rollback:
--   DROP INDEX IF EXISTS public.idx_miniapp_rate_limits_bucket;
--   DROP INDEX IF EXISTS public.idx_miniapp_idempotency_lease;
--   ALTER TABLE public.miniapp_idempotency_keys
--     DROP COLUMN IF EXISTS last_error_code,
--     DROP COLUMN IF EXISTS attempt_count,
--     DROP COLUMN IF EXISTS lease_expires_at;

ALTER TABLE public.miniapp_idempotency_keys
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_error_code TEXT;

UPDATE public.miniapp_idempotency_keys
   SET lease_expires_at = COALESCE(updated_at, created_at, now()) + interval '5 minutes',
       attempt_count = GREATEST(attempt_count, 1)
 WHERE status = 'pending'
   AND lease_expires_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_miniapp_idempotency_lease
    ON public.miniapp_idempotency_keys(status, lease_expires_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_miniapp_rate_limits_bucket
    ON public.miniapp_rate_limits(bucket);
