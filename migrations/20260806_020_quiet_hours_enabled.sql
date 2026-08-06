ALTER TABLE public.notification_preferences
  ADD COLUMN IF NOT EXISTS quiet_hours_enabled BOOLEAN;

UPDATE public.notification_preferences
   SET quiet_hours_enabled = CASE
       WHEN quiet_hours_start IS NOT NULL AND quiet_hours_end IS NOT NULL THEN true
       ELSE false
   END
 WHERE quiet_hours_enabled IS NULL;

ALTER TABLE public.notification_preferences
  ALTER COLUMN quiet_hours_enabled SET DEFAULT false;

ALTER TABLE public.notification_preferences
  ALTER COLUMN quiet_hours_enabled SET NOT NULL;
