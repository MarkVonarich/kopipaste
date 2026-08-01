-- Finuchet: make challenge notifications explicit opt-in and suppress old unsent challenge prompts.
-- Production command:
--   psql "$DATABASE_URL" -f migrations/20260801_013_challenge_notifications_opt_in.sql
-- Do not apply from Codex; run through the deployment process.

BEGIN;

ALTER TABLE public.notification_preferences
    ADD COLUMN IF NOT EXISTS challenge_notifications_enabled BOOLEAN DEFAULT FALSE;

ALTER TABLE public.notification_preferences
    ALTER COLUMN challenge_notifications_enabled SET DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS public.notification_rollouts (
    rollout_key TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM public.notification_rollouts
         WHERE rollout_key = 'challenge_notifications_default_off_20260801'
    ) THEN
        UPDATE public.notification_preferences
           SET challenge_notifications_enabled = FALSE
         WHERE challenge_notifications_enabled IS DISTINCT FROM FALSE;

        UPDATE public.automatic_notifications
           SET status = 'skipped',
               skip_reason = 'challenge_notifications_default_off_rollout',
               locked_at = NULL,
               locked_by = NULL,
               updated_at = now()
         WHERE notification_type IN ('challenge_prompt', 'challenge_completed', 'achievement_granted')
           AND status IN ('pending', 'claimed');

        INSERT INTO public.notification_rollouts (rollout_key)
        VALUES ('challenge_notifications_default_off_20260801');
    END IF;
END $$;

COMMIT;

-- Rollback strategy:
--   ALTER TABLE public.notification_preferences
--       ALTER COLUMN challenge_notifications_enabled SET DEFAULT TRUE;
--   UPDATE public.notification_rollouts
--      SET applied_at = applied_at
--    WHERE rollout_key = 'challenge_notifications_default_off_20260801';
-- Rollback intentionally does not revive skipped challenge notification rows
-- and does not reset user challenge progress or achievements.
